# Dual Counting Test (Camera-event vs YOLO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trên trang chi tiết camera, hiển thị song song 2 bộ đếm người ra/vào hôm nay (Camera Axis event vs YOLO local) + nút reset baseline dùng chung + form cấu hình vạch YOLO, để so sánh độ chính xác.

**Architecture:** Block Camera tái dùng pipeline `events`(`type='counter'`) + `event_collector` có sẵn. Block YOLO là engine mới **độc lập** trong `monitor.py` (lifecycle riêng, tự capture RTSP full-FPS bằng cv2, `model.track(persist=True)`, line-crossing + dead-band) ghi `events`(`type='counter_yolo'`). Reset lưu bảng `counting_baseline` dùng chung 2 block. Logic thuần (line-crossing math, block-from-counts) tách ra `counting.py` để unit-test; phần DB/engine/UI verify bằng cách chạy app.

**Tech Stack:** FastAPI, psycopg v3 (ConnectionPool, dict_row), PostgreSQL, ultralytics YOLOv8 (`model.track`), OpenCV (cv2), Jinja2 + vanilla JS/CSS, pytest (chỉ cho logic thuần).

## Global Constraints

- **App:** `fall_detection_web/` — KHÔNG bump version add-on (đây là app standalone).
- **Commit + push sau mỗi thay đổi nguồn** (theo AGENTS.md). Commit message tiếng Việt.
- **DB = PostgreSQL** qua `db.get_conn()` (context manager, auto-commit, `dict_row`). KHÔNG dùng SQLite, KHÔNG migration framework — schema tường minh trong `init_db()`.
- **Thời gian VN+7**: lọc "hôm nay" theo `(ts AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date`. `db.LOCAL_TZ = timezone(timedelta(hours=7))`.
- **occupancy luôn clamp `max(0, in-out)`**.
- **Regression-safe:** query Phase 1 (`/counting`) lọc `type='counter'` → KHÔNG được đụng tới; YOLO dùng `type='counter_yolo'` riêng.
- **Auth:** mọi route gắn `_: str = Depends(auth.require_auth)`.
- **Pure logic** trong `counting.py` chỉ dùng stdlib (no DB, no cv2, no torch) — để unit-test.
- **Test:** repo không có DB test harness. Chỉ logic thuần dùng pytest (`tests/test_counting.py`). Task DB/API/engine/UI verify bằng lệnh chạy thật (psql/curl/chạy app) — ghi rõ trong từng task.
- **No test suite cho cv2/torch path** — engine verify bằng quan sát log + bảng `events`.

---

## File Structure

- `fall_detection_web/counting.py` — **MODIFY**: thêm pure helpers `block_from_counts`, `side_of`, `resolve_side`, `crossing_direction`, `in_x_range`.
- `fall_detection_web/tests/test_counting.py` — **MODIFY**: thêm test cho các helper trên.
- `fall_detection_web/db.py` — **MODIFY**: `init_db()` thêm bảng `counting_baseline` + cột `cameras.yolo_counting JSONB`; thêm hàm `insert_counting_event`, `counting_block`, `get_counting_baseline`, `set_counting_baseline`, `get_yolo_counting`, `set_yolo_counting`, `list_yolo_counting_cameras`.
- `fall_detection_web/monitor.py` — **MODIFY**: engine đếm YOLO độc lập (`start_counting`/`stop_counting`/`restart_counting`/`_counting_loop` + globals).
- `fall_detection_web/app.py` — **MODIFY**: 3 endpoint counting per-camera; wiring lifespan + refresh.
- `fall_detection_web/templates/camera_detail.html` — **MODIFY**: 2 block + form cấu hình thu gọn + nút reset + polling JS + CSS.

---

## Task 1: Pure logic helpers trong counting.py (TDD)

**Files:**
- Modify: `fall_detection_web/counting.py`
- Test: `fall_detection_web/tests/test_counting.py`

**Interfaces:**
- Produces:
  - `block_from_counts(raw_in: int, raw_out: int, baseline: int = 0) -> dict` → `{"in","out","occupancy"}` với `in = baseline+raw_in`, `out = raw_out`, `occupancy = max(0, in-out)`.
  - `side_of(cy: float, y_line: float, band: float) -> str | None` → `"above"` nếu `cy < y_line-band`, `"below"` nếu `cy > y_line+band`, else `None` (trong dead-band).
  - `resolve_side(prev: str | None, cy: float, y_line: float, band: float) -> str | None` → side mới, giữ `prev` nếu đang trong dead-band.
  - `crossing_direction(prev_side: str | None, new_side: str | None, invert: bool = False) -> str | None` → `"in"`/`"out"`/`None`. above→below = `in` (đảo nếu `invert`).
  - `in_x_range(cx: float, frame_w: float, x_start_pct: float, x_end_pct: float) -> bool`.

- [ ] **Step 1: Viết test thất bại** — thêm vào cuối `tests/test_counting.py`:

```python
from counting import (
    block_from_counts, side_of, resolve_side, crossing_direction, in_x_range,
)


def test_block_from_counts_basic():
    assert block_from_counts(3, 1) == {"in": 3, "out": 1, "occupancy": 2}


def test_block_from_counts_baseline_sets_in_only():
    # reset N=5 -> in = 5 + new_in, out = new_out, occ clamp
    assert block_from_counts(2, 0, baseline=5) == {"in": 7, "out": 0, "occupancy": 7}


def test_block_from_counts_occupancy_clamped():
    assert block_from_counts(1, 4)["occupancy"] == 0


def test_side_of_dead_band_returns_none():
    assert side_of(100, 100, 10) is None      # ngay vạch -> dead-band
    assert side_of(85, 100, 10) == "above"     # cy < 90
    assert side_of(115, 100, 10) == "below"    # cy > 110


def test_resolve_side_keeps_prev_in_dead_band():
    assert resolve_side("above", 100, 100, 10) == "above"   # vẫn trong band
    assert resolve_side("above", 115, 100, 10) == "below"   # đã qua band


def test_crossing_direction_above_to_below_is_in():
    assert crossing_direction("above", "below") == "in"
    assert crossing_direction("below", "above") == "out"


def test_crossing_direction_invert():
    assert crossing_direction("above", "below", invert=True) == "out"


def test_crossing_direction_no_change_or_none():
    assert crossing_direction("above", "above") is None
    assert crossing_direction(None, "below") is None
    assert crossing_direction("above", None) is None


def test_in_x_range():
    # frame_w=1000, 40%..70% -> [400,700]
    assert in_x_range(500, 1000, 40, 70) is True
    assert in_x_range(399, 1000, 40, 70) is False
    assert in_x_range(701, 1000, 40, 70) is False
```

- [ ] **Step 2: Chạy test để chắc chắn FAIL**

Run: `cd fall_detection_web && python -m pytest tests/test_counting.py -q`
Expected: FAIL — `ImportError: cannot import name 'block_from_counts'`.

- [ ] **Step 3: Cài đặt tối thiểu** — thêm vào cuối `counting.py`:

```python
# ── Dual-counting test helpers (block compute + YOLO line-crossing) ──

def block_from_counts(raw_in: int, raw_out: int, baseline: int = 0) -> dict:
    """Số hiển thị 1 block: baseline cộng vào IN, OUT giữ nguyên, occupancy clamp."""
    ins = int(baseline) + int(raw_in)
    outs = int(raw_out)
    return {"in": ins, "out": outs, "occupancy": max(0, ins - outs)}


def side_of(cy: float, y_line: float, band: float) -> str | None:
    """Phía của tâm so với vạch, với dead-band ±band. None = đang trong band."""
    if cy < y_line - band:
        return "above"
    if cy > y_line + band:
        return "below"
    return None


def resolve_side(prev: str | None, cy: float, y_line: float, band: float) -> str | None:
    s = side_of(cy, y_line, band)
    return s if s is not None else prev


def crossing_direction(prev_side: str | None, new_side: str | None,
                       invert: bool = False) -> str | None:
    """above→below = 'in' (đảo nếu invert). None nếu chưa đổi side rõ ràng."""
    if prev_side is None or new_side is None or prev_side == new_side:
        return None
    direction = "in" if (prev_side == "above" and new_side == "below") else "out"
    if invert:
        direction = "out" if direction == "in" else "in"
    return direction


def in_x_range(cx: float, frame_w: float, x_start_pct: float, x_end_pct: float) -> bool:
    x0 = x_start_pct / 100.0 * frame_w
    x1 = x_end_pct / 100.0 * frame_w
    return x0 <= cx <= x1
```

- [ ] **Step 4: Chạy test để chắc chắn PASS**

Run: `cd fall_detection_web && python -m pytest tests/test_counting.py -q`
Expected: PASS (toàn bộ test cũ + mới, ~13 test).

- [ ] **Step 5: Commit**

```bash
git add fall_detection_web/counting.py fall_detection_web/tests/test_counting.py
git commit -m "feat(counting): pure helpers cho dual-counting (block + line-crossing)"
```

---

## Task 2: Schema — bảng counting_baseline + cột cameras.yolo_counting

**Files:**
- Modify: `fall_detection_web/db.py` (trong `init_db()`, sau khối Phase 3 ~ dòng 233)

**Interfaces:**
- Produces: bảng `counting_baseline(cam_id PK, reset_ts, baseline)`; cột `cameras.yolo_counting JSONB DEFAULT '{}'::jsonb`.

- [ ] **Step 1: Thêm DDL vào `init_db()`** — chèn ngay sau câu `UPDATE cameras SET counting_enabled=true, live_enabled=true WHERE cam_uid='B8A44F4627CE'` (kết thúc khối Phase 3, trước khi hàm `init_db` đóng):

```python
        # ── Dual-counting test: baseline reset + cấu hình vạch YOLO per-camera ──
        conn.execute(
            "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS yolo_counting JSONB "
            "NOT NULL DEFAULT '{}'::jsonb"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS counting_baseline (
                cam_id    INT PRIMARY KEY REFERENCES cameras(id),
                reset_ts  TIMESTAMPTZ NOT NULL,
                baseline  INT NOT NULL CHECK (baseline >= 0)
            )
        """)
```

- [ ] **Step 2: Verify schema áp dụng** (cần Postgres chạy: `docker compose up -d db`):

Run:
```bash
cd fall_detection_web && python -c "import db; db.init_db(); \
import db as d; \
print(d.get_conn().__enter__().execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='cameras' AND column_name='yolo_counting'\").fetchall()); \
print(d.get_conn().__enter__().execute(\"SELECT to_regclass('counting_baseline')\").fetchall())"
```
Expected: in ra `[{'column_name': 'yolo_counting'}]` và `[{'to_regclass': 'counting_baseline'}]`.

- [ ] **Step 3: Commit**

```bash
git add fall_detection_web/db.py
git commit -m "feat(db): schema counting_baseline + cameras.yolo_counting cho dual-counting"
```

---

## Task 3: db.py — hàm read/write cho counting + baseline + cấu hình vạch

**Files:**
- Modify: `fall_detection_web/db.py` (thêm sau `counting_crossings`, ~ dòng 741)

**Interfaces:**
- Consumes: `counting.block_from_counts` (Task 1); `get_conn`, `LOCAL_TZ`.
- Produces:
  - `insert_counting_event(cam_id: int, direction: str, ts: datetime, source: str = "yolo", track_id: str | None = None) -> None` — INSERT 1 row `events`. `source='yolo'`→`type='counter_yolo'`, `source='axis'`→`type='counter'`.
  - `counting_block(cam_id: int, source: str, since_ts: datetime | None = None, baseline_in: int = 0) -> dict` — đếm in/out hôm nay VN cho `type` tương ứng (lọc `cam_id`, optional `ts > since_ts`) → `block_from_counts`.
  - `get_counting_baseline(cam_id: int) -> dict | None` — `{"reset_ts": datetime, "baseline": int}` hoặc None.
  - `set_counting_baseline(cam_id: int, reset_ts: datetime, baseline: int) -> None` — upsert.
  - `get_yolo_counting(cam_id: int) -> dict` — JSONB cfg (dict rỗng nếu chưa set).
  - `set_yolo_counting(cam_id: int, cfg: dict) -> None`.
  - `list_yolo_counting_cameras() -> list[dict]` — cameras `enabled=true AND (yolo_counting->>'enabled')::bool = true`, mỗi dict gồm `id, name, rtsp_url, go2rtc_src, yolo_counting`.

- [ ] **Step 1: Thêm import Json** — ở đầu `db.py`, sau `from psycopg.rows import dict_row` (dòng 21) thêm:

```python
from psycopg.types.json import Json
```

- [ ] **Step 2: Thêm các hàm** sau `counting_crossings` (dòng 741):

```python
_SOURCE_TYPE = {"yolo": "counter_yolo", "axis": "counter"}

_VN_TODAY = ("(e.ts AT TIME ZONE 'Asia/Ho_Chi_Minh')::date "
             "= (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date")


def insert_counting_event(cam_id: int, direction: str, ts: datetime,
                          source: str = "yolo", track_id: str | None = None) -> None:
    """Ghi 1 crossing vào bảng events (dùng cho YOLO; source='axis' nếu cần)."""
    etype = _SOURCE_TYPE.get(source, "counter_yolo")
    axis_obj = f"yolo-{track_id}" if track_id is not None else None
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events (cam_id, ts, type, direction, axis_object_id, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (cam_id, ts, etype, direction, axis_obj,
             Json({"source": source, "track_id": track_id})),
        )


def counting_block(cam_id: int, source: str, since_ts: datetime | None = None,
                   baseline_in: int = 0) -> dict[str, int]:
    """IN/OUT/occupancy hôm nay VN cho 1 nguồn (counter | counter_yolo), 1 camera."""
    import counting as _counting
    etype = _SOURCE_TYPE.get(source, "counter_yolo")
    where = f"e.cam_id = %s AND e.type = %s AND {_VN_TODAY}"
    params: list[Any] = [cam_id, etype]
    if since_ts is not None:
        where += " AND e.ts > %s"
        params.append(since_ts)
    sql = (
        "SELECT COUNT(*) FILTER (WHERE e.direction = 'in')  AS ins, "
        "COUNT(*) FILTER (WHERE e.direction = 'out') AS outs "
        f"FROM events e WHERE {where}"
    )
    with get_conn() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return _counting.block_from_counts(int(row["ins"] or 0), int(row["outs"] or 0), baseline_in)


def get_counting_baseline(cam_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT reset_ts, baseline FROM counting_baseline WHERE cam_id = %s",
            (cam_id,),
        ).fetchone()
    return {"reset_ts": row["reset_ts"], "baseline": int(row["baseline"])} if row else None


def set_counting_baseline(cam_id: int, reset_ts: datetime, baseline: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO counting_baseline (cam_id, reset_ts, baseline) VALUES (%s, %s, %s) "
            "ON CONFLICT (cam_id) DO UPDATE SET reset_ts = EXCLUDED.reset_ts, "
            "baseline = EXCLUDED.baseline",
            (cam_id, reset_ts, max(0, int(baseline))),
        )


def get_yolo_counting(cam_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT yolo_counting FROM cameras WHERE id = %s", (cam_id,)
        ).fetchone()
    return dict(row["yolo_counting"]) if row and row["yolo_counting"] else {}


def set_yolo_counting(cam_id: int, cfg: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE cameras SET yolo_counting = %s WHERE id = %s",
            (Json(cfg), cam_id),
        )


def list_yolo_counting_cameras() -> list[dict[str, Any]]:
    """Cameras active có yolo_counting.enabled = true (cho engine đếm YOLO)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, rtsp_url, go2rtc_src, yolo_counting FROM cameras "
            "WHERE enabled = true AND COALESCE((yolo_counting->>'enabled')::bool, false) = true "
            "ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 3: Verify bằng smoke test** (Postgres đang chạy, cam Axis id=1 đã seed):

Run:
```bash
cd fall_detection_web && python -c "
import db, datetime as dt
db.init_db()
cid = 1
db.set_yolo_counting(cid, {'enabled': True, 'line_y': 51, 'x_start': 44, 'x_end': 71, 'min_disp': 6, 'invert': False})
print('cfg:', db.get_yolo_counting(cid))
print('list:', [c['id'] for c in db.list_yolo_counting_cameras()])
now = dt.datetime.now(dt.timezone.utc)
db.insert_counting_event(cid, 'in', now, 'yolo', track_id='7')
print('yolo block:', db.counting_block(cid, 'yolo'))
db.set_counting_baseline(cid, now, 5)
print('baseline:', db.get_counting_baseline(cid))
print('axis block:', db.counting_block(cid, 'axis'))
"
```
Expected: `cfg` in ra dict đã set; `list` chứa `1`; `yolo block` có `in>=1`; `baseline` = `{'reset_ts':..., 'baseline':5}`; không lỗi.

- [ ] **Step 4: Verify KHÔNG đụng trang /counting cũ** (counter_yolo không lọt vào query Phase 1):

Run:
```bash
cd fall_detection_web && python -c "import db; print('counting_occupancy_today (chỉ counter):', db.counting_occupancy_today())"
```
Expected: occupancy KHÔNG tính row `counter_yolo` vừa insert (vì query lọc `type='counter'`).

- [ ] **Step 5: Commit**

```bash
git add fall_detection_web/db.py
git commit -m "feat(db): hàm counting_block/baseline/yolo_counting cho dual-counting"
```

---

## Task 4: API endpoints per-camera trong app.py

**Files:**
- Modify: `fall_detection_web/app.py` (thêm sau `api_counting`, ~ dòng 175)

**Interfaces:**
- Consumes: `config.read_config`, `find_camera_by_name` (trả `(index, camera)`, `camera["id"]` = cam_id DB); `db.counting_block`, `db.get_counting_baseline`, `db.set_counting_baseline`, `db.get_yolo_counting`, `db.set_yolo_counting`; `db.LOCAL_TZ`.
- Produces (đường dẫn đặt `{camera_name:path}` ở CUỐI để khỏi nuốt segment):
  - `GET /api/counting/camera/{camera_name:path}` → `{"date","camera":{in,out,occupancy},"yolo":{in,out,occupancy},"reset_ts"}`.
  - `POST /api/counting/reset/{camera_name:path}` body `{"occupancy": int}` → set baseline, trả số mới.
  - `POST /api/counting/yolo-config/{camera_name:path}` body cfg → validate + lưu + restart engine.

- [ ] **Step 1: Thêm helper resolve + 3 route** sau `api_counting` (dòng 175):

```python
def _cam_id_by_name(camera_name: str) -> tuple[int, dict[str, Any]]:
    c = config.read_config()
    _, camera = find_camera_by_name(c, camera_name)
    cam_id = camera.get("id")
    if cam_id is None:
        raise HTTPException(status_code=404, detail="Camera chưa có trong registry")
    return int(cam_id), camera


def _counting_blocks(cam_id: int) -> dict[str, Any]:
    from datetime import datetime, timezone, timedelta
    vn = timezone(timedelta(hours=7))
    base = db.get_counting_baseline(cam_id)
    vn_today = datetime.now(vn).date()
    since_ts = None
    baseline_in = 0
    reset_ts_iso = None
    if base and base["reset_ts"].astimezone(vn).date() == vn_today:
        since_ts = base["reset_ts"]
        baseline_in = base["baseline"]
        reset_ts_iso = base["reset_ts"].astimezone(vn).strftime("%H:%M:%S")
    return {
        "date": vn_today.isoformat(),
        "camera": db.counting_block(cam_id, "axis", since_ts, baseline_in),
        "yolo": db.counting_block(cam_id, "yolo", since_ts, baseline_in),
        "reset_ts": reset_ts_iso,
    }


@app.get("/api/counting/camera/{camera_name:path}")
def api_counting_camera(camera_name: str, _: str = Depends(auth.require_auth)):
    cam_id, _camera = _cam_id_by_name(camera_name)
    return _counting_blocks(cam_id)


@app.post("/api/counting/reset/{camera_name:path}")
def api_counting_reset(camera_name: str, payload: dict[str, Any] = Body(...),
                       _: str = Depends(auth.require_auth)):
    cam_id, _camera = _cam_id_by_name(camera_name)
    try:
        occupancy = max(0, int(payload.get("occupancy", 0)))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="occupancy phải là số nguyên >= 0")
    from datetime import datetime, timezone
    db.set_counting_baseline(cam_id, datetime.now(timezone.utc), occupancy)
    return _counting_blocks(cam_id)


@app.post("/api/counting/yolo-config/{camera_name:path}")
def api_counting_yolo_config(camera_name: str, payload: dict[str, Any] = Body(...),
                             _: str = Depends(auth.require_auth)):
    cam_id, _camera = _cam_id_by_name(camera_name)

    def _pct(key: str, default: float) -> float:
        try:
            return min(100.0, max(0.0, float(payload.get(key, default))))
        except (TypeError, ValueError):
            return default

    x_start = _pct("x_start", 0)
    x_end = _pct("x_end", 100)
    if x_start >= x_end:
        raise HTTPException(status_code=400, detail="x_start phải nhỏ hơn x_end")
    cfg = {
        "enabled": bool(payload.get("enabled", False)),
        "line_y": _pct("line_y", 50),
        "x_start": x_start,
        "x_end": x_end,
        "min_disp": _pct("min_disp", 6),
        "invert": bool(payload.get("invert", False)),
    }
    db.set_yolo_counting(cam_id, cfg)
    monitor.restart_counting(config.read_config())
    return {"ok": True, "yolo_counting": cfg}
```

> **Lưu ý import:** `Body` đã import sẵn (dùng ở `api_update_camera_modules`). Nếu thiếu, thêm `Body` vào dòng `from fastapi import ...`.

- [ ] **Step 2: Verify bằng curl** (app chạy `uvicorn app:app --port 8090`, đăng nhập lấy cookie). Vì route có auth, dùng cookie JWT từ trình duyệt hoặc tạm bỏ qua: test sau khi Task 6 chạy UI. Tối thiểu kiểm route tồn tại (401 thay vì 404):

Run:
```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8090/api/counting/camera/cam_door"
```
Expected: `401` (route tồn tại, chỉ thiếu auth) — KHÔNG phải `404`.

- [ ] **Step 3: Commit**

```bash
git add fall_detection_web/app.py
git commit -m "feat(api): endpoint counting/reset/yolo-config per-camera cho dual-counting"
```

---

## Task 5: Engine đếm YOLO độc lập trong monitor.py

> **Lệch spec có chủ đích:** spec viết "nhét vào _monitor_loop, đọc ké frame_holders". Khi audit phát hiện (a) `_monitor_loop` chỉ chạy khi có camera `fall_detection_enabled` → couple sai; (b) capture go2rtc ~1fps → tracking kém. Vì vậy engine đếm là **lifecycle độc lập**, tự mở RTSP bằng cv2 (full-FPS), model riêng mỗi cam (tracker state độc lập → đa-cam an toàn). Vẫn dùng pattern reconnect-backoff như `capture_latest_frames`.

**Files:**
- Modify: `fall_detection_web/monitor.py` (globals gần dòng 32; hàm mới đặt sau `restart_monitor`, ~ dòng 1245)

**Interfaces:**
- Consumes: `db.list_yolo_counting_cameras`, `db.insert_counting_event`; `counting.resolve_side`, `counting.crossing_direction`, `counting.in_x_range`.
- Produces: `start_counting(config) -> None`, `stop_counting(wait: bool = False) -> None`, `restart_counting(config) -> None`, `_counting_loop(camera: dict, line_cfg: dict) -> None`.

- [ ] **Step 1: Thêm globals** — sau `monitor_lock = threading.Lock()` (dòng 34):

```python
counting_stop_event = threading.Event()
counting_threads: list[threading.Thread] = []
counting_lock = threading.Lock()
```

- [ ] **Step 2: Thêm engine** — sau `restart_monitor` (cuối khối lifecycle, ~ dòng 1245):

```python
# ── Engine đếm YOLO độc lập (dual-counting test) ──

def _counting_loop(camera: dict[str, Any], line_cfg: dict[str, Any]) -> None:
    """Mở RTSP full-FPS, model.track(persist=True), line-crossing + dead-band → ghi events(counter_yolo)."""
    import cv2
    from ultralytics import YOLO
    import counting as _counting

    cam_id = int(camera["id"])
    cam_name = str(camera.get("name") or cam_id)
    rtsp_url = str(camera.get("rtsp_url") or "")
    if not rtsp_url:
        logger.warning("[COUNT] camera=%s không có rtsp_url, bỏ qua đếm YOLO", cam_name)
        return

    cfg = read_config()
    model = YOLO(cfg["yolo_model"])
    imgsz = int(cfg["yolo_imgsz"])
    conf = float(cfg["confidence"])

    line_y_pct = float(line_cfg.get("line_y", 50))
    x_start = float(line_cfg.get("x_start", 0))
    x_end = float(line_cfg.get("x_end", 100))
    min_disp_pct = float(line_cfg.get("min_disp", 6))
    invert = bool(line_cfg.get("invert", False))

    track_sides: dict[int, str] = {}
    cap = cv2.VideoCapture(rtsp_url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    consecutive_failures = 0
    last_reconnect_log = 0.0
    logger.info("[COUNT] start camera=%s line_y=%.0f%% x=[%.0f,%.0f]%% min_disp=%.0f%% invert=%s",
                cam_name, line_y_pct, x_start, x_end, min_disp_pct, invert)
    try:
        while not counting_stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                consecutive_failures += 1
                now = time.time()
                if now - last_reconnect_log > 30:
                    logger.warning("[COUNT] RTSP read failed camera=%s (failures=%s), reconnect",
                                   cam_name, consecutive_failures)
                    last_reconnect_log = now
                cap.release()
                if counting_stop_event.wait(min(2.0 ** consecutive_failures, 30.0)):
                    break
                cap = cv2.VideoCapture(rtsp_url)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                continue
            consecutive_failures = 0

            h, w = frame.shape[:2]
            y_line = line_y_pct / 100.0 * h
            band = min_disp_pct / 100.0 * h

            results = model.track(frame, persist=True, classes=[0], conf=conf,
                                  imgsz=imgsz, verbose=False)
            seen_ids: set[int] = set()
            for result in results:
                boxes = result.boxes
                if boxes is None or boxes.id is None:
                    continue
                ids = boxes.id.int().tolist()
                xyxy = boxes.xyxy.tolist()
                for tid, (x1, y1, x2, y2) in zip(ids, xyxy):
                    cx = (x1 + x2) / 2.0
                    cy = (y1 + y2) / 2.0
                    if not _counting.in_x_range(cx, w, x_start, x_end):
                        continue
                    seen_ids.add(tid)
                    prev = track_sides.get(tid)
                    new_side = _counting.resolve_side(prev, cy, y_line, band)
                    direction = _counting.crossing_direction(prev, new_side, invert)
                    if direction:
                        db.insert_counting_event(
                            cam_id, direction,
                            datetime.now(timezone.utc), "yolo", track_id=str(tid))
                        logger.info("[COUNT] camera=%s track=%s -> %s", cam_name, tid, direction)
                    if new_side is not None:
                        track_sides[tid] = new_side
            # dọn rác id không còn track (tránh phình bộ nhớ)
            if len(track_sides) > 256:
                for dead in [k for k in track_sides if k not in seen_ids]:
                    track_sides.pop(dead, None)
    except Exception:
        logger.exception("[COUNT] loop failed camera=%s", cam_name)
    finally:
        cap.release()
        logger.info("[COUNT] stop camera=%s", cam_name)


def start_counting(config: dict[str, Any]) -> None:
    """Khởi động 1 thread đếm cho mỗi camera có yolo_counting.enabled."""
    with counting_lock:
        if any(t.is_alive() for t in counting_threads):
            return
        cams = db.list_yolo_counting_cameras()
        if not cams:
            return
        counting_stop_event.clear()
        counting_threads.clear()
        for cam in cams:
            line_cfg = dict(cam.get("yolo_counting") or {})
            t = threading.Thread(target=_counting_loop, args=(cam, line_cfg), daemon=True)
            t.start()
            counting_threads.append(t)
        logger.info("[COUNT] engine started for %s camera(s)", len(counting_threads))


def stop_counting(wait: bool = False) -> None:
    counting_stop_event.set()
    if wait:
        for t in counting_threads:
            if t.is_alive():
                t.join(timeout=8)
    counting_threads.clear()


def restart_counting(config: dict[str, Any]) -> None:
    stop_counting(wait=True)
    start_counting(config)
```

> **Lưu ý:** `datetime`, `timezone`, `time`, `threading`, `db`, `read_config`, `logger` đều đã import sẵn ở đầu `monitor.py` (đã dùng trong file). Nếu `read_config` chưa import, dùng `from config import read_config` ở đầu file (kiểm tra: `_monitor_loop`/`capture_latest_frames` đã gọi `config[...]` truyền vào — nếu file chưa import `read_config`, thêm `from config import read_config`).

- [ ] **Step 3: Verify engine chạy 1 cam thật** (Postgres + RTSP cam reachable; đã set cfg ở Task 3 Step 3):

Run:
```bash
cd fall_detection_web && timeout 60 python -c "
import db, monitor, config, time
db.init_db()
monitor.start_counting(config.read_config())
print('threads:', [t.is_alive() for t in monitor.counting_threads])
time.sleep(45)   # đi qua cửa vài lần trong lúc này
monitor.stop_counting(wait=True)
print('yolo block sau test:', db.counting_block(1, 'yolo'))
"
```
Expected: `threads: [True]`; log `[COUNT] start ...` và `[COUNT] ... -> in/out` khi có người qua vạch; `yolo block` có số > 0. (Nếu RTSP không tới được → log reconnect; xác nhận tối thiểu thread sống + không crash.)

- [ ] **Step 4: Commit**

```bash
git add fall_detection_web/monitor.py
git commit -m "feat(monitor): engine đếm YOLO độc lập (track + line-crossing + dead-band)"
```

---

## Task 6: Wiring lifespan + refresh engine khi đổi cấu hình

**Files:**
- Modify: `fall_detection_web/app.py` (lifespan ~ dòng 67-77; `_refresh_monitor_after_camera_change` ~ dòng 508)

**Interfaces:**
- Consumes: `monitor.start_counting`, `monitor.stop_counting`, `monitor.restart_counting`.

- [ ] **Step 1: Auto-start trong lifespan** — sau khối auto-start monitor (sau dòng 72, trước `yield` dòng 74) thêm:

```python
    # Auto-start engine đếm YOLO (độc lập với monitor fall-detect)
    try:
        monitor.start_counting(current_config)
    except Exception as exc:
        logger.error("Could not auto-start counting engine: %s", exc)
```

- [ ] **Step 2: Dừng khi shutdown** — sau `monitor.stop_monitor(wait=True)` (dòng 77) thêm:

```python
    monitor.stop_counting(wait=True)
```

- [ ] **Step 3: Refresh engine khi camera thay đổi** — trong `_refresh_monitor_after_camera_change` (dòng 508), trước `return`, thêm restart engine (đổi flag/registry có thể bật/tắt đếm):

```python
    try:
        monitor.restart_counting(updated)
    except Exception as exc:
        logger.warning("counting engine restart failed: %s", exc)
```
(Chèn ngay trước `if monitor.read_state().get("running"):` để luôn chạy bất kể monitor fall-detect có chạy hay không.)

- [ ] **Step 4: Verify boot** — chạy app, xem log:

Run: `cd fall_detection_web && uvicorn app:app --host 0.0.0.0 --port 8090` (Ctrl-C sau khi thấy log)
Expected: nếu cam Axis đã bật `yolo_counting.enabled` → log `[COUNT] engine started for 1 camera(s)`; app khởi động không lỗi.

- [ ] **Step 5: Commit**

```bash
git add fall_detection_web/app.py
git commit -m "feat(app): auto-start/stop/restart engine đếm YOLO theo lifecycle"
```

---

## Task 7: UI — 2 block + form cấu hình thu gọn + reset + polling

**Files:**
- Modify: `fall_detection_web/templates/camera_detail.html` (CSS trong `<style>`; markup trong template `cameraMetrics` ~ dòng 725-728; JS sau `loadCamera` ~ dòng 748)

**Interfaces:**
- Consumes: `GET /api/counting/camera/{name}`, `POST /api/counting/reset/{name}`, `POST /api/counting/yolo-config/{name}`; hàm `api()`, `$()`, `routeCameraName`, `state` có sẵn.

- [ ] **Step 1: Thêm CSS** — trong `<style>` (gần `.metric-card` dòng 194) thêm:

```css
    .count-block { grid-column: span 2; border-radius: 8px; padding: 12px 14px; border-left: 4px solid; }
    .count-block.cam  { background: rgba(55,138,221,0.12);  border-left-color: #378ADD; }
    .count-block.yolo { background: rgba(239,159,39,0.12);  border-left-color: #EF9F27; }
    .count-block h4 { margin: 0 0 8px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; display: flex; align-items: center; gap: 6px; }
    .count-block.cam h4  { color: #378ADD; }
    .count-block.yolo h4 { color: #EF9F27; }
    .count-stats { display: flex; gap: 18px; }
    .count-stats .n { font-family: "Fira Code", monospace; font-size: 24px; font-weight: 700; color: var(--text); }
    .count-stats .k { font-size: 11px; color: var(--muted); text-transform: uppercase; }
    .count-cfg { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px; }
    .count-cfg summary { cursor: pointer; font-size: 12px; color: var(--muted); }
    .count-cfg .row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
    .count-cfg label { font-size: 12px; color: var(--muted); width: 130px; }
    .count-cfg input[type=number] { width: 72px; }
    .count-reset { grid-column: span 2; display: flex; align-items: center; gap: 8px; margin-top: 4px; }
    .count-reset .k { font-size: 12px; color: var(--muted); flex: 1; }
```

- [ ] **Step 2: Thêm markup** — trong template literal của `$("cameraMetrics").innerHTML` (renderLive), NGAY SAU card go2rtc Source (sau dòng 728 `</div>` đóng card đó, trước backtick kết thúc):

```javascript
        <div class="count-block cam">
          <h4><span data-icon="link-icon"></span>Camera (Axis) · hôm nay</h4>
          <div class="count-stats">
            <div><div class="n" id="camIn">–</div><div class="k">Vào</div></div>
            <div><div class="n" id="camOut">–</div><div class="k">Ra</div></div>
            <div><div class="n" id="camOcc">–</div><div class="k">Trong phòng</div></div>
          </div>
        </div>
        <div class="count-block yolo">
          <h4><span data-icon="monitor"></span>YOLO (máy local) · hôm nay</h4>
          <div class="count-stats">
            <div><div class="n" id="yoloIn">–</div><div class="k">Vào</div></div>
            <div><div class="n" id="yoloOut">–</div><div class="k">Ra</div></div>
            <div><div class="n" id="yoloOcc">–</div><div class="k">Trong phòng</div></div>
          </div>
          <details class="count-cfg">
            <summary>⚙ Cấu hình vạch đếm YOLO</summary>
            <div class="row"><label>Bật đếm YOLO</label><input type="checkbox" id="cfgEnabled"></div>
            <div class="row"><label>Vạch ngang Y (%)</label><input type="number" id="cfgLineY" min="0" max="100" value="51"></div>
            <div class="row"><label>Đoạn X bắt đầu (%)</label><input type="number" id="cfgXStart" min="0" max="100" value="44"></div>
            <div class="row"><label>Đoạn X kết thúc (%)</label><input type="number" id="cfgXEnd" min="0" max="100" value="71"></div>
            <div class="row"><label>Dịch chuyển tối thiểu (%)</label><input type="number" id="cfgMinDisp" min="0" max="30" value="6"></div>
            <div class="row"><label>Đảo chiều (xuống=Ra)</label><input type="checkbox" id="cfgInvert"></div>
            <div class="row"><button class="btn" onclick="saveYoloConfig()">Lưu vạch</button></div>
          </details>
        </div>
        <div class="count-reset">
          <span class="k">Reset cả 2 block về số người đang trong phòng:</span>
          <input type="number" id="resetOcc" min="0" value="0" style="width:72px">
          <button class="btn" onclick="resetCounting()">Đặt lại hôm nay</button>
        </div>
```

- [ ] **Step 3: Thêm JS** — sau `loadCamera` (dòng 748) thêm các hàm + khởi động poll. Trong `loadCamera`, sau `renderLive();` (dòng 746) thêm gọi `loadCounting(); startCountingPoll();`:

```javascript
    function setCountVal(id, v) { const el = $(id); if (el) el.textContent = (v ?? "–"); }
    async function loadCounting() {
      try {
        const d = await api(`/api/counting/camera/${encodeURIComponent(routeCameraName)}`);
        setCountVal("camIn", d.camera.in);  setCountVal("camOut", d.camera.out);  setCountVal("camOcc", d.camera.occupancy);
        setCountVal("yoloIn", d.yolo.in);   setCountVal("yoloOut", d.yolo.out);   setCountVal("yoloOcc", d.yolo.occupancy);
        // nạp cấu hình vạch hiện tại từ camera (chỉ khi form chưa mở để khỏi đè khi đang sửa)
        const cfg = (state.camera && state.camera.yolo_counting) || {};
        const det = document.querySelector(".count-cfg");
        if (det && !det.open) {
          if ($("cfgEnabled")) $("cfgEnabled").checked = !!cfg.enabled;
          if (cfg.line_y != null && $("cfgLineY")) $("cfgLineY").value = cfg.line_y;
          if (cfg.x_start != null && $("cfgXStart")) $("cfgXStart").value = cfg.x_start;
          if (cfg.x_end != null && $("cfgXEnd")) $("cfgXEnd").value = cfg.x_end;
          if (cfg.min_disp != null && $("cfgMinDisp")) $("cfgMinDisp").value = cfg.min_disp;
          if ($("cfgInvert")) $("cfgInvert").checked = !!cfg.invert;
        }
      } catch (e) { /* fail-open: giữ "–" */ }
    }
    function startCountingPoll() {
      if (state.countTimer) clearInterval(state.countTimer);
      state.countTimer = setInterval(loadCounting, 3000);
    }
    async function resetCounting() {
      const occupancy = parseInt($("resetOcc").value || "0", 10) || 0;
      try {
        await api(`/api/counting/reset/${encodeURIComponent(routeCameraName)}`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ occupancy }) });
        showToast("Đã đặt lại số đếm hôm nay", "ok");
        await loadCounting();
      } catch (e) { showToast(e.message || "Lỗi reset", "bad"); }
    }
    async function saveYoloConfig() {
      const cfg = {
        enabled: $("cfgEnabled").checked,
        line_y: parseFloat($("cfgLineY").value),
        x_start: parseFloat($("cfgXStart").value),
        x_end: parseFloat($("cfgXEnd").value),
        min_disp: parseFloat($("cfgMinDisp").value),
        invert: $("cfgInvert").checked,
      };
      try {
        await api(`/api/counting/yolo-config/${encodeURIComponent(routeCameraName)}`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) });
        showToast("Đã lưu vạch đếm YOLO", "ok");
        if (state.camera) state.camera.yolo_counting = cfg;
      } catch (e) { showToast(e.message || "Lỗi lưu cấu hình", "bad"); }
    }
```

> **Lưu ý:** để `state.camera.yolo_counting` có dữ liệu, `/api/camera/detail` cần trả cột này. `camera` lấy từ `config.read_config()` → `cameras_from_table` hiện KHÔNG có `yolo_counting`. Xử lý ở Step 4.

- [ ] **Step 4: Đưa yolo_counting vào camera dict** — trong `fall_detection_web/config.py`, hàm `cameras_from_table` (dòng 332-352), thêm field vào dict `cam`:

```python
            "yolo_counting": dict(r.get("yolo_counting") or {}),
```
(thêm ngay sau dòng `"id": r.get("id"),`).

> `cameras_for_config()` dùng `SELECT *` nên `r` đã có cột `yolo_counting`.

- [ ] **Step 5: Verify UI thủ công** — chạy app, mở `http://localhost:8090/camera/cam_door` (login admin/admin):
  - Dưới go2rtc Source hiện 2 block xanh/cam, mỗi block 3 số, cập nhật mỗi 3s.
  - Mở `⚙ Cấu hình vạch` → sửa số → Lưu → toast OK; refresh thấy giá trị giữ nguyên.
  - Nhập số vào ô reset → Đặt lại → cả 2 block về `Vào=N, Ra=0, Trong phòng=N`.
  - Đi qua cửa thật → số YOLO tăng; đối chiếu số Axis.

Expected: tất cả mục trên hoạt động; trang `/counting` cũ không đổi.

- [ ] **Step 6: Commit**

```bash
git add fall_detection_web/templates/camera_detail.html fall_detection_web/config.py
git commit -m "feat(ui): 2 block đếm + form cấu hình vạch + reset trên trang chi tiết cam"
```

---

## Self-Review

**Spec coverage:**
- §2 UI 2 block + reset + form thu gọn → Task 7. ✅
- §3.1 YOLO → events `counter_yolo` → Task 3 (`insert_counting_event`), Task 5 (engine). ✅
- §3.2 baseline `counting_baseline` dùng chung → Task 2 (schema), Task 3 (get/set), Task 4 (reset áp cả 2 block qua `_counting_blocks`). ✅
- §3.3 công thức block (baseline + window) → Task 1 (`block_from_counts`), Task 3 (`counting_block`), Task 4 (`_counting_blocks`). ✅
- §3.4 cfg JSONB `cameras.yolo_counting` → Task 2 (cột), Task 3 (get/set), Task 4 (validate), Task 7 Step 4 (expose). ✅
- §4 engine track + line + X-range + dead-band → Task 1 (pure), Task 5 (loop). ✅
- §5 endpoints → Task 4. ✅
- §6 ops dependency (event_collector) → KHÔNG code (đã ghi spec); block Camera fail-open hiện `–`/0. ✅
- §7 YAGNI: không chart, không vẽ canvas, không đa-cam tracker đặc biệt (per-thread model đã đủ), không recount log. ✅
- §9 tiêu chí hoàn thành → phủ bởi verify Task 5/7. ✅

**Lệch spec (ghi rõ, có lý do):** Engine đếm là lifecycle ĐỘC LẬP + tự capture RTSP (không nhét `_monitor_loop`/`frame_holders`) — lý do ở đầu Task 5. Hệ quả tốt: chạy được kể cả khi không bật fall-detect, tracking full-FPS, đa-cam an toàn.

**Placeholder scan:** không có TBD/TODO; mọi step có code/lệnh cụ thể. ✅

**Type consistency:** `source` dùng `"yolo"`/`"axis"` xuyên suốt (Task 3 `_SOURCE_TYPE`, Task 4 `counting_block(cam_id,"axis"/"yolo")`, Task 5 `insert_counting_event(...,"yolo")`). Block dict luôn `{in,out,occupancy}`. cfg keys `enabled/line_y/x_start/x_end/min_disp/invert` khớp giữa Task 4 validate, Task 5 đọc, Task 7 form. `cam_id` = `camera["id"]` (int) nhất quán. ✅
</content>
