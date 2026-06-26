# Phase 2: Module Group / Re-ID — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port khả năng Group/Re-ID (gom lượt theo người) từ `dcnet-cloud/camera` vào camera-ai như **module tùy chọn, TẮT mặc định** (build-and-shelve) — schema pgvector + `reid_worker` async + trang "Nhóm theo người".

**Architecture:** `reid_worker` = service async riêng (aiomqtt+asyncpg+OSNet/InsightFace), parallel với `event_collector` Phase 1. Worker ghi `person_group`/`appearance`/`appearance_crop` + crop files; FDW (psycopg sync) chỉ ĐỌC + serve crop qua FileResponse. Worker KHÔNG start trừ khi `docker compose --profile reid up`. Default: nav ẩn, trang hiện banner "module chưa bật".

**Tech Stack:** Python 3.12; worker: aiomqtt+asyncpg+structlog+numpy+opencv-headless+onnxruntime+insightface+torch+torchreid; FDW: FastAPI+psycopg+Jinja2; PostgreSQL 16 pgvector (`vector(512)`, ivfflat).

## Global Constraints

- **Build-and-shelve:** module TẮT mặc định. 2 blocker chưa giải (cam dome placement NO-GO + OSNet/InsightFace non-commercial license). Spec = implement, KHÔNG activate.
- **arm64 build DEFER (quyết định review 2026-06-26):** KHÔNG build heavy torch image trên arm64 Mac (Phase 0 ghi torch+cpu build blocked). Phase 2 "done" = schema + pure unit tests (numpy-only, NO torch) + db queries + UI mock-data + crop route + compose config-validate. Build image + live worker defer x86/activate.
- **Pure tests chạy KHÔNG cần torch:** `embed.py` import torch/torchreid/insightface/cv2 LAZILY (trong method) — module top chỉ numpy. 4 test file (`test_parser`/`test_matcher`/`test_assembler`/`test_embed_fuse`) import chỉ pure helpers. Chạy với venv `numpy+pytest`, KHÔNG cài torch.
- **Schema vào `init_db()`** (pattern Phase 1, `CREATE TABLE IF NOT EXISTS`, idempotent). FK `person_group.cam_id → cameras(id)` (Phase 1).
- **Chỉ 3 bảng Re-ID:** `person_group`/`appearance`/`appearance_crop`. KHÔNG port `employees`/`embeddings`/`face_pool`/`recognitions`/`attendance_sessions` (recognition/attendance scope riêng, branch `main-backup-2306`).
- **Volume constraint (CRITICAL):** worker + FDW PHẢI mount CÙNG named volume `fdw_data` tại CÙNG path `/app/data`. Worker `DATA_DIR=/app/data` (đổi từ DCNET default `/data`). FDW `REID_CROPS_DIR = DATA_DIR / "reid_crops"`. Sai → crop route 404 toàn bộ.
- **Worker edits vs DCNET:** `REID_FACE_ENABLED` default `false` (DCNET `true`); `MQTT_CLIENT_ID` default `reid_worker_cameraai` (DUY NHẤT, ko đá prod); `_dsn()` prefer `DATABASE_URL`; `DATA_DIR` default `/app/data`; OQ2 file-purge trong `purge_loop`.
- **`REID_COMMERCIAL_MODE=true` → `sys.exit(1)`** guard giữ nguyên (main.py).
- **MQTT broker:** cloud `camera-test.dcnet.vn:8883` TLS; topic `poc/objsnap` (cam publisher `pocsnap`, đã DELETE 2026-06-25 — chỉ có message khi recreate).
- **Crop route auth + path-validation:** JWT như `/api/event-image`; reject `..`/non-`.jpg`/non-int group_id → 404. KHÔNG StaticFiles mount.
- **psycopg pattern (FDW đọc):** `with get_conn() as conn: conn.execute(sql, params).fetchall()`, `%s`, `dict_row`. asyncpg (worker): `$n`, `::vector` cast cho vector literal.
- **KHÔNG đổi hàm db.py/app.py cũ.** Chỉ thêm.

---

## File Structure

**Create (worker service — cp verbatim từ DCNET + edits):**
- `services/reid_worker/src/reid_worker/{__init__,parser,matcher,assembler,embed,repo,main}.py`
- `services/reid_worker/tests/{test_parser,test_matcher,test_assembler,test_embed_fuse}.py`
- `services/reid_worker/{Dockerfile,requirements.txt}`

**Modify:**
- `fall_detection_web/db.py` — `init_db()` thêm 3 bảng Re-ID; thêm `REID_CROPS_DIR`, `reid_live_groups`, `reid_group_crops`, `reid_stats`
- `fall_detection_web/app.py` — routes `/groups`, `/api/groups`, `/api/reid-crop/{group_id}/{filename}` (trước catch-all `/{page_name}`)
- `fall_detection_web/templates/groups.html` (Create)
- `fall_detection_web/templates/index.html` — nav link (ẩn khi disabled)
- `docker-compose.yml` — service `reid_worker` (profile `reid`)

**Source repo (cp from):** `/Users/vovanduc/Code/dcnet/camera/services/reid_worker/` và `.../services/dashboard/src/dashboard/groups.py`.

---

## Task 1: Schema Re-ID (3 tables) trong init_db()

Append 3 bảng pgvector vào `init_db()` sau section Phase 1 (cameras/events). Verify bằng smoke psql + insert mock vector.

**Files:**
- Modify: `fall_detection_web/db.py` (cuối thân `with get_conn()` trong `init_db()`)

**Interfaces:**
- Consumes: `cameras(id)` (Phase 1), `CREATE EXTENSION vector` (Phase 0).
- Produces: tables `person_group`, `appearance`, `appearance_crop`.

- [ ] **Step 1: Thêm schema vào `init_db()`**

Thêm vào CUỐI thân `with get_conn() as conn:` trong `init_db()` (sau khối seed cam Phase 1):

```python
        # ── Phase 2: Re-ID group schema (module optional, OFF mặc định) ──
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS person_group (
                id               BIGSERIAL PRIMARY KEY,
                cam_id           INT REFERENCES cameras(id),
                first_seen       TIMESTAMPTZ NOT NULL,
                last_seen        TIMESTAMPTZ NOT NULL,
                visit_count      INT NOT NULL DEFAULT 1,
                rep_body_vector  vector(512) NOT NULL,
                rep_face_vector  vector(512),
                rep_crop_path    TEXT,
                created_at       TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS person_group_last_seen ON person_group (last_seen DESC)")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS person_group_body_ivf ON person_group
            USING ivfflat (rep_body_vector vector_cosine_ops) WITH (lists = 100)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS appearance (
                id           BIGSERIAL PRIMARY KEY,
                group_id     BIGINT REFERENCES person_group(id) ON DELETE CASCADE,
                cam_id       INT REFERENCES cameras(id),
                ts           TIMESTAMPTZ NOT NULL,
                body_vector  vector(512) NOT NULL,
                face_vector  vector(512),
                track_id     TEXT,
                created_at   TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS appearance_group ON appearance (group_id, ts DESC)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS appearance_crop (
                id             BIGSERIAL PRIMARY KEY,
                appearance_id  BIGINT REFERENCES appearance(id) ON DELETE CASCADE,
                kind           TEXT NOT NULL CHECK (kind IN ('body','face')),
                path           TEXT NOT NULL,
                frame_idx      INT,
                quality        REAL,
                created_at     TIMESTAMPTZ DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS appearance_crop_app ON appearance_crop (appearance_id)")
```

- [ ] **Step 2: Smoke — init_db tạo 3 bảng + insert mock vector round-trip**

Postgres dev phải up (`docker compose up -d postgres`). Chạy:

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai/fall_detection_web
DATABASE_URL=postgresql://dcnet:dcnet_dev@localhost:5432/dcnet python3 -c "
import db
db.init_db()
with db.get_conn() as c:
    print('tables:', sorted(r['table_name'] for r in c.execute(
        \"SELECT table_name FROM information_schema.tables WHERE table_name IN ('person_group','appearance','appearance_crop')\").fetchall()))
    vec = '[' + ','.join(['0.1']*512) + ']'
    cam = c.execute('SELECT id FROM cameras LIMIT 1').fetchone()['id']
    gid = c.execute(\"INSERT INTO person_group (cam_id,first_seen,last_seen,rep_body_vector) VALUES (%s,now(),now(),%s::vector) RETURNING id\", (cam, vec)).fetchone()['id']
    print('inserted person_group id:', gid)
    c.execute('DELETE FROM person_group WHERE id=%s', (gid,))
"
```

Expected: `tables: ['appearance', 'appearance_crop', 'person_group']`; `inserted person_group id:` int. Không exception (đặc biệt `::vector` cast OK → pgvector hoạt động).

- [ ] **Step 3: Verify idempotent (chạy init_db 2 lần)**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai/fall_detection_web
DATABASE_URL=postgresql://dcnet:dcnet_dev@localhost:5432/dcnet python3 -c "import db; db.init_db(); db.init_db(); print('idempotent ok')"
```

Expected: `idempotent ok`, không lỗi.

- [ ] **Step 4: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add fall_detection_web/db.py
git commit -m "feat(phase2): schema Re-ID person_group/appearance/appearance_crop (pgvector)"
```

---

## Task 2: `reid_worker` service port (cp verbatim + edits) + pure tests

Copy 6 src + 4 test từ DCNET verbatim, apply edits cho `main.py` (4 env defaults + OQ2 purge) và `repo.py` (purge return ids). Chạy 4 pure test trong venv numpy-only. **KHÔNG build image** (defer arm64/torch).

**Files:**
- Create (cp verbatim): `services/reid_worker/src/reid_worker/{__init__,parser,matcher,assembler,embed}.py` + 4 `tests/test_*.py` + `requirements.txt`
- Create (cp + edit): `services/reid_worker/src/reid_worker/{repo,main}.py`, `services/reid_worker/Dockerfile`

**Interfaces:**
- Consumes: tables Task 1; `cameras` Phase 1; cloud broker MQTT.
- Produces (port verbatim, pure): `parse_objsnap(payload)->dict|None`; `cosine(a,b)`, `decide_match(body_vec,groups,thr)`; `Assembler(track_timeout_ms)` `.add/.flush_expired/.flush_all`; `l2norm/fuse_embeddings/best_quality_idx/body_crop_ok`. Live: `BodyEmbedder/FaceEmbedder/embed_appearance`; `ReidRepo` asyncpg.

- [ ] **Step 1: Copy verbatim src + tests + requirements từ DCNET**

```bash
SRC=/Users/vovanduc/Code/dcnet/camera/services/reid_worker
DST=/Users/vovanduc/Code/dcnet/camera-ai/services/reid_worker
mkdir -p "$DST/src/reid_worker" "$DST/tests"
cp "$SRC/src/reid_worker/__init__.py" "$DST/src/reid_worker/"
cp "$SRC/src/reid_worker/parser.py" "$DST/src/reid_worker/"
cp "$SRC/src/reid_worker/matcher.py" "$DST/src/reid_worker/"
cp "$SRC/src/reid_worker/assembler.py" "$DST/src/reid_worker/"
cp "$SRC/src/reid_worker/embed.py" "$DST/src/reid_worker/"
cp "$SRC/src/reid_worker/repo.py" "$DST/src/reid_worker/"
cp "$SRC/src/reid_worker/main.py" "$DST/src/reid_worker/"
cp "$SRC/tests/test_parser.py" "$DST/tests/"
cp "$SRC/tests/test_matcher.py" "$DST/tests/"
cp "$SRC/tests/test_assembler.py" "$DST/tests/"
cp "$SRC/tests/test_embed_fuse.py" "$DST/tests/"
cp "$SRC/requirements.txt" "$DST/"
ls -R "$DST/src" "$DST/tests"
```

⚠️ **KHÔNG copy** `raw_capture.py` (diagnostic, không thuộc pipeline). Expected: 7 src + 4 test files listed.

- [ ] **Step 2: Chạy 4 pure test (numpy-only venv, NO torch) — verify PASS**

Pure helpers chỉ cần numpy. Tạo venv nhẹ:

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai/services/reid_worker
python3 -m venv /tmp/reid_pure_venv
/tmp/reid_pure_venv/bin/pip install -q numpy==1.26.4 pytest
PYTHONPATH=src /tmp/reid_pure_venv/bin/python -m pytest tests/ -v
```

Expected: tất cả test PASS (parser/matcher/assembler/embed_fuse). KHÔNG `ModuleNotFoundError: torch` (vì embed.py import torch lazily). Nếu lỗi torch → 1 test import sai, fix.

- [ ] **Step 3: Edit `main.py` — 4 env defaults + OQ2 purge file deletion**

Trong `services/reid_worker/src/reid_worker/main.py`:

(a) Thêm `import shutil` (cạnh `import sys`).

(b) `DATA_DIR` default `/data` → `/app/data` (line ~42):
```python
DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR", "/app/data"))
```

(c) `FACE_ENABLED` default `true` → `false` (line ~48):
```python
FACE_ENABLED = os.environ.get("REID_FACE_ENABLED", "false").lower() == "true"
```

(d) `_dsn()` prefer `DATABASE_URL` (lines ~56-58):
```python
def _dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return (f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
            f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}")
```

(e) `consume_loop` client_id default `reid_worker` → `reid_worker_cameraai` (line ~162):
```python
    client_id = os.environ.get("MQTT_CLIENT_ID", "reid_worker_cameraai")
```

(f) OQ2 — `purge_loop` xóa crop files của group hết hạn (lines ~134-142). Thay nguyên hàm:
```python
async def purge_loop(repo: ReidRepo) -> None:
    while True:
        await asyncio.sleep(300)  # 5 phút
        try:
            gids = await repo.purge_expired(TTL_HOURS)
            for gid in gids:
                shutil.rmtree(CROP_DIR / str(gid), ignore_errors=True)
            if gids:
                log.info("purged_expired_groups", count=len(gids))
        except Exception:
            log.exception("purge_failed")
```

- [ ] **Step 4: Edit `repo.py` — `purge_expired` trả list group_ids (cho OQ2)**

Trong `services/reid_worker/src/reid_worker/repo.py`, thay nguyên `purge_expired` (lines ~113-120):
```python
    async def purge_expired(self, ttl_hours: float) -> list[int]:
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "DELETE FROM person_group WHERE last_seen < now() - ($1 || ' hours')::interval "
                "RETURNING id",
                str(ttl_hours),
            )
        return [int(r["id"]) for r in rows]
```

- [ ] **Step 5: Re-run pure tests (đảm bảo edits ko vỡ import)**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai/services/reid_worker
PYTHONPATH=src /tmp/reid_pure_venv/bin/python -m pytest tests/ -v
```

Expected: vẫn PASS (edits ở main.py/repo.py — không bị test pure import; main.py import torch lazily nhưng tests không import main).

- [ ] **Step 6: Create `Dockerfile` (cp + edit) — KHÔNG build**

Copy DCNET Dockerfile + edit `INSIGHTFACE_HOME` sang `/app/data` (khớp DATA_DIR volume) + thêm comment defer/commercial:

```bash
cp /Users/vovanduc/Code/dcnet/camera/services/reid_worker/Dockerfile \
   /Users/vovanduc/Code/dcnet/camera-ai/services/reid_worker/Dockerfile
```

Sau đó edit `services/reid_worker/Dockerfile`: đổi dòng `INSIGHTFACE_HOME=/data/.insightface` → `INSIGHTFACE_HOME=/app/data/.insightface`, và thêm comment ngay sau `FROM`:
```dockerfile
FROM python:3.12-slim
# ⚠️ Module OFF mặc định (build-and-shelve). Stack OSNet/InsightFace = non-commercial:
#    KHÔNG enable cho khách trả tiền tới khi swap permissive (xem spec phase2 §9 OQ1).
# ⚠️ Image nặng (~3-4GB torch). Build defer x86 (arm64 torch build blocked — Phase 0).
```

⚠️ **KHÔNG chạy `docker compose build reid_worker`** (defer). Verification của task này = pure tests pass + files present.

- [ ] **Step 7: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add services/reid_worker
git commit -m "feat(phase2): port reid_worker (OFF default, FACE off, client-id, OQ2 purge); pure tests pass, image build deferred"
```

---

## Task 3: docker-compose service `reid_worker` (profile reid) — config validate, no build

Thêm service với profile `reid` (opt-in), volume `fdw_data:/app/data` (CRITICAL), env. Verify bằng `docker compose config` (KHÔNG build, KHÔNG up).

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: volume `fdw_data` (Phase 1 FDW), `postgres` healthcheck.

- [ ] **Step 1: Thêm service `reid_worker`**

Thêm block sau (cùng indent với `event_collector:`), trước khối top-level `volumes:`:

```yaml
  reid_worker:
    build: ./services/reid_worker
    profiles: ["reid"]          # opt-in: KHÔNG start mặc định
    env_file:
      - path: ./.env
        required: false
    environment:
      DATABASE_URL: postgresql://dcnet:${DB_PASSWORD:-dcnet_dev}@postgres:5432/dcnet
      MQTT_HOST: ${MQTT_HOST:-camera-test.dcnet.vn}
      MQTT_PORT: ${MQTT_PORT:-8883}
      MQTT_TLS: ${MQTT_TLS:-true}
      MQTT_USER: ${MQTT_USER:-}
      MQTT_PASSWORD: ${MQTT_PASSWORD:-}
      MQTT_CLIENT_ID: reid_worker_cameraai
      REID_FACE_ENABLED: "false"
      REID_TOPIC: poc/objsnap
      DATA_DIR: /app/data       # PHẢI khớp FDW DATA_DIR (cùng volume fdw_data)
    volumes:
      - fdw_data:/app/data      # CÙNG named volume + CÙNG path với fall_detection_web
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
```

- [ ] **Step 2: Validate compose (KHÔNG build/up) + xác nhận volume khớp FDW**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
docker compose config >/dev/null && echo "compose valid"
echo "=== reid_worker NOT in default profile ===" 
docker compose config --services | grep -q reid_worker && echo "in default (WRONG)" || echo "profile-gated (correct)"
echo "=== reid_worker IS in 'reid' profile ===" 
docker compose --profile reid config --services | grep -q reid_worker && echo "in reid profile (correct)"
echo "=== volume + DATA_DIR match FDW ===" 
docker compose --profile reid config | grep -A2 -E 'reid_worker|fall_detection_web' | grep -E 'fdw_data|/app/data' | head
```

Expected: `compose valid`; `profile-gated (correct)`; `in reid profile (correct)`; cả 2 service hiện `fdw_data` + `/app/data`. ⚠️ KHÔNG build/up — chỉ config validate.

- [ ] **Step 3: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add docker-compose.yml
git commit -m "feat(phase2): compose service reid_worker (profile reid, shared fdw_data volume)"
```

---

## Task 4: `db.py` read funcs (FDW sync) + `REID_CROPS_DIR`

3 hàm đọc groups (psycopg sync, dict_row) + hằng `REID_CROPS_DIR`. Smoke với mock data từ Task 1.

**Files:**
- Modify: `fall_detection_web/db.py` (thêm hằng cạnh `EVENT_IMAGES_DIR`; thêm 3 hàm cuối file)

**Interfaces:**
- Consumes: tables Task 1; `get_conn()`, `DATA_DIR` (db.py).
- Produces:
  - `REID_CROPS_DIR: Path = DATA_DIR / "reid_crops"`
  - `reid_live_groups(ttl_hours=2, cam_id=None) -> list[dict]` — keys `id, visit_count, first_seen, last_seen, rep_crop_path`
  - `reid_group_crops(group_id, limit=40) -> list[dict]` — keys `kind, path, quality, ts`
  - `reid_stats(ttl_hours=2) -> dict` — keys `unique_count, reentry_count`

- [ ] **Step 1: Thêm hằng `REID_CROPS_DIR`**

Trong `fall_detection_web/db.py`, cạnh `EVENT_IMAGES_DIR = DATA_DIR / "event_images"`:
```python
REID_CROPS_DIR = DATA_DIR / "reid_crops"
```

- [ ] **Step 2: Thêm 3 hàm đọc cuối db.py**

```python
# ── Re-ID groups (Phase 2, read-only; worker ghi) ──

def reid_live_groups(ttl_hours: float = 2, cam_id: int | None = None) -> list[dict[str, Any]]:
    where = "last_seen >= now() - (%s || ' hours')::interval"
    params: list[Any] = [str(ttl_hours)]
    if cam_id is not None:
        where += " AND cam_id = %s"
        params.append(cam_id)
    sql = (
        "SELECT id, visit_count, first_seen, last_seen, rep_crop_path "
        f"FROM person_group WHERE {where} ORDER BY last_seen DESC"
    )
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def reid_group_crops(group_id: int, limit: int = 40) -> list[dict[str, Any]]:
    sql = (
        "SELECT ac.kind, ac.path, ac.quality, a.ts "
        "FROM appearance a JOIN appearance_crop ac ON ac.appearance_id = a.id "
        "WHERE a.group_id = %s ORDER BY a.ts DESC, ac.kind LIMIT %s"
    )
    with get_conn() as conn:
        rows = conn.execute(sql, (group_id, limit)).fetchall()
    return [dict(r) for r in rows]


def reid_stats(ttl_hours: float = 2) -> dict[str, int]:
    sql = (
        "SELECT COUNT(*) AS unique_count, "
        "COUNT(*) FILTER (WHERE visit_count > 1) AS reentry_count "
        "FROM person_group WHERE last_seen >= now() - (%s || ' hours')::interval"
    )
    with get_conn() as conn:
        row = conn.execute(sql, (str(ttl_hours),)).fetchone()
    return {"unique_count": int(row["unique_count"] or 0),
            "reentry_count": int(row["reentry_count"] or 0)}
```

- [ ] **Step 3: Smoke — insert mock group, gọi 3 hàm, verify, cleanup**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai/fall_detection_web
DATABASE_URL=postgresql://dcnet:dcnet_dev@localhost:5432/dcnet python3 -c "
import db
db.init_db()
vec = '[' + ','.join(['0.1']*512) + ']'
with db.get_conn() as c:
    cam = c.execute('SELECT id FROM cameras LIMIT 1').fetchone()['id']
    gid = c.execute(\"INSERT INTO person_group (cam_id,first_seen,last_seen,visit_count,rep_body_vector,rep_crop_path) VALUES (%s,now(),now(),2,%s::vector,'/app/data/reid_crops/x/1_body_0.jpg') RETURNING id\", (cam, vec)).fetchone()['id']
    aid = c.execute(\"INSERT INTO appearance (group_id,cam_id,ts,body_vector) VALUES (%s,%s,now(),%s::vector) RETURNING id\", (gid, cam, vec)).fetchone()['id']
    c.execute(\"INSERT INTO appearance_crop (appearance_id,kind,path,frame_idx,quality) VALUES (%s,'body','/app/data/reid_crops/x/1_body_0.jpg',0,0.8)\", (aid,))
print('live_groups:', db.reid_live_groups())
print('crops:', db.reid_group_crops(gid))
print('stats:', db.reid_stats())
print('REID_CROPS_DIR:', db.REID_CROPS_DIR)
with db.get_conn() as c:
    c.execute('DELETE FROM person_group WHERE id=%s', (gid,))
"
```

Expected: `live_groups:` 1 dict (visit_count=2, rep_crop_path set); `crops:` 1 dict (kind body, quality 0.8); `stats:` `{'unique_count': 1, 'reentry_count': 1}`; `REID_CROPS_DIR:` ends `/data/reid_crops`. Cleanup CASCADE xóa appearance+crop.

- [ ] **Step 4: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add fall_detection_web/db.py
git commit -m "feat(phase2): db.py reid read queries (live_groups/group_crops/stats) + REID_CROPS_DIR"
```

---

## Task 5: Routes `/groups` + `/api/groups` + `/api/reid-crop/` + template + nav (banner khi OFF)

Routes (trước catch-all), Jinja template (port từ Streamlit page), crop serving (FileResponse + path-validation), nav link ẩn khi `REID_ENABLED=false`. Verify mock data + path traversal 404.

**Files:**
- Modify: `fall_detection_web/app.py` (import; 3 route trước catch-all `/{page_name}`)
- Create: `fall_detection_web/templates/groups.html`
- Modify: `fall_detection_web/templates/index.html` (nav link)

**Interfaces:**
- Consumes: `db.reid_live_groups/reid_group_crops/reid_stats/REID_CROPS_DIR` (Task 4); `auth.require_auth`, `templates`, `FileResponse` (đã import ở app.py cho event-image).

- [ ] **Step 1: Thêm 3 route vào app.py (TRƯỚC catch-all `/{page_name}`)**

`os` đã import ở app.py. Thêm các route NGAY TRƯỚC `@app.get("/{page_name}")`:

```python
def _reid_enabled() -> bool:
    return os.environ.get("REID_ENABLED", "false").lower() == "true"


@app.get("/groups", response_class=HTMLResponse)
def groups_page(request: Request, _: str = Depends(auth.require_auth)):
    return templates.TemplateResponse(
        request=request, name="groups.html",
        context={"reid_enabled": _reid_enabled()})


@app.get("/api/groups")
def api_groups(_: str = Depends(auth.require_auth)):
    from datetime import timezone, timedelta
    vn = timezone(timedelta(hours=7))
    groups = db.reid_live_groups()
    stats = db.reid_stats()
    out = []
    for g in groups:
        gid = g["id"]
        crops = db.reid_group_crops(gid)
        rep = g.get("rep_crop_path")
        rep_name = Path(rep).name if rep else None
        out.append({
            "id": gid,
            "visit_count": g["visit_count"],
            "is_reentry": g["visit_count"] > 1,
            "badge": "🔁 ĐÃ VÀO RỒI" if g["visit_count"] > 1 else "🆕 Khách mới",
            "first_seen": g["first_seen"].astimezone(vn).strftime("%H:%M:%S %d/%m"),
            "last_seen": g["last_seen"].astimezone(vn).strftime("%H:%M:%S %d/%m"),
            "rep_crop": rep_name,
            "crops": [
                {"kind": c["kind"],
                 "name": Path(c["path"]).name,
                 "quality": round(float(c["quality"]), 2) if c["quality"] is not None else None,
                 "ts": c["ts"].astimezone(vn).strftime("%H:%M:%S")}
                for c in crops
            ],
        })
    return {"reid_enabled": _reid_enabled(), "stats": stats, "groups": out}


@app.get("/api/reid-crop/{group_id}/{filename}")
def reid_crop(group_id: str, filename: str, _: str = Depends(auth.require_auth)):
    if not group_id.isdigit():
        raise HTTPException(status_code=404, detail="Not found")
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.lower().endswith(".jpg"):
        raise HTTPException(status_code=404, detail="Not found")
    path = db.REID_CROPS_DIR / group_id / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400, immutable"})
```

⚠️ Xác nhận `FileResponse` và `Path` đã import ở đầu app.py (đã dùng trong `/api/event-image`). Nếu thiếu, thêm import.

- [ ] **Step 2: Tạo `templates/groups.html`**

Port từ Streamlit page sang Jinja + vanilla JS polling (15s, OQ5). Self-contained, style như counting.html Phase 1:

```html
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nhóm theo người — DCNET Camera</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.ico">
  <style>
    :root { --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8;
            --acc:#38bdf8; --new:#22c55e; --re:#f59e0b; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text);
           font-family:system-ui,-apple-system,sans-serif; padding:24px; }
    a { color:var(--acc); text-decoration:none; }
    h1 { font-size:1.4rem; margin:0 0 4px; }
    .cap { color:var(--muted); font-size:.85rem; margin:0 0 16px; }
    .banner { background:rgba(245,158,11,.15); border:1px solid var(--re);
              border-radius:10px; padding:14px 16px; margin-bottom:20px; color:#fde68a; }
    .nums { display:grid; grid-template-columns:repeat(2,1fr); gap:16px; margin-bottom:24px; max-width:520px; }
    .num { background:var(--card); border-radius:12px; padding:18px; text-align:center; }
    .num .v { font-size:2.2rem; font-weight:700; }
    .num .l { color:var(--muted); font-size:.82rem; margin-top:4px; }
    .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }
    .gcard { background:var(--card); border-radius:12px; padding:12px; }
    .gcard img.rep { width:100%; border-radius:8px; aspect-ratio:1/2; object-fit:cover;
                     background:#0b1220; }
    .gcard .noimg { width:100%; aspect-ratio:1/2; border-radius:8px; background:#0b1220;
                    display:flex; align-items:center; justify-content:center;
                    color:var(--muted); font-size:.8rem; }
    .badge { font-weight:700; font-size:.85rem; margin:8px 0 2px; }
    .badge.re { color:var(--re); } .badge.new { color:var(--new); }
    .gcard .meta { color:var(--muted); font-size:.72rem; }
    details { margin-top:8px; }
    details summary { cursor:pointer; color:var(--acc); font-size:.8rem; }
    .crops { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .crops figure { margin:0; width:64px; }
    .crops img { width:64px; height:96px; object-fit:cover; border-radius:4px; background:#0b1220; }
    .crops figcaption { font-size:.6rem; color:var(--muted); }
    @media (max-width:900px){ .grid{grid-template-columns:repeat(2,1fr);} }
  </style>
</head>
<body>
  <p><a href="/">← Dashboard</a></p>
  <h1>👥 Nhóm theo người</h1>
  <p class="cap">Gom lượt của cùng 1 người trong 2h gần nhất (ẩn danh). Badge 🔁 = đã vào rồi.</p>
  {% if not reid_enabled %}
  <div class="banner">⚠️ Module Re-ID <b>chưa bật</b> (TẮT mặc định: cam dome NO-GO + license non-commercial).
    Bật = <code>docker compose --profile reid up -d reid_worker</code> + <code>REID_ENABLED=true</code>.
    Xem spec <code>docs/superpowers/specs/2026-06-26-phase2-group-reid-design.md §2.6</code>.</div>
  {% endif %}
  <div class="nums">
    <div class="num"><div class="v" id="uniq">–</div><div class="l">Khách duy nhất (2h)</div></div>
    <div class="num"><div class="v" id="reentry">–</div><div class="l">Số lượt tái xuất</div></div>
  </div>
  <div class="grid" id="grid"></div>
  <script>
    function crop(gid, name) { return `/api/reid-crop/${gid}/${encodeURIComponent(name)}`; }
    async function refresh() {
      try {
        const r = await fetch("/api/groups", { credentials: "same-origin" });
        if (!r.ok) return;
        const d = await r.json();
        document.getElementById("uniq").textContent = d.stats.unique_count;
        document.getElementById("reentry").textContent = d.stats.reentry_count;
        const grid = document.getElementById("grid");
        if (!d.groups.length) {
          grid.innerHTML = '<p style="color:var(--muted)">Chưa có nhóm nào trong cửa sổ thời gian.</p>';
          return;
        }
        grid.innerHTML = d.groups.map(g => `
          <div class="gcard">
            ${g.rep_crop
              ? `<img class="rep" src="${crop(g.id, g.rep_crop)}" alt="rep" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'noimg',textContent:'(ảnh lỗi)'}))">`
              : `<div class="noimg">(chưa có ảnh)</div>`}
            <div class="badge ${g.is_reentry ? 're' : 'new'}">${g.badge}</div>
            <div class="meta">Số lần vào: <b>${g.visit_count}</b></div>
            <div class="meta">Đầu: ${g.first_seen} · Gần nhất: ${g.last_seen}</div>
            <details>
              <summary>Xem crop (${g.crops.length})</summary>
              <div class="crops">${g.crops.map(c => `
                <figure>
                  <img src="${crop(g.id, c.name)}" alt="${c.kind}" onerror="this.style.display='none'">
                  <figcaption>${c.kind} · ${c.quality ?? '–'} · ${c.ts}</figcaption>
                </figure>`).join("")}</div>
            </details>
          </div>`).join("");
      } catch (e) { /* giữ giá trị cũ khi lỗi */ }
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
```

- [ ] **Step 3: Thêm nav link (ẩn khi disabled) vào index.html**

Trong `fall_detection_web/templates/index.html`, nav block, thêm sau link "Đếm ra/vào" (Phase 1). Vì index.html render không có `reid_enabled` context, dùng link tĩnh nhưng trang `/groups` tự hiện banner khi OFF → đơn giản nhất (spec §5g cho phép):

```html
        <a class="nav-btn" href="/groups"><span data-icon="users"></span>Nhóm theo người</a>
```

- [ ] **Step 4: Verify — render + crop serving + path traversal (mock data)**

Postgres up + insert mock như Task 4 Step 3 (giữ 1 group + tạo 1 file crop giả). Rebuild FDW:

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
# tạo file crop giả trong volume FDW (group_id sẽ lấy từ mock insert)
docker compose up -d --build fall_detection_web
```

Verify (chưa login → auth chặn):
- `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8090/groups` → `302` (auth, route tồn tại, trước catch-all — KHÔNG 404).
- `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8090/api/groups` → `401`.
- Path-traversal (auth chặn trước nên test logic qua unit-style; sau login): `/api/reid-crop/1/../../etc/passwd` → 404; `/api/reid-crop/abc/x.jpg` → 404 (group_id không phải digit); `/api/reid-crop/1/x.txt` → 404 (không .jpg).
- Sau login (browser): `/groups` render — banner "module chưa bật" (REID_ENABLED unset), 2 số, grid. Insert mock group (Task 4) + tạo file `docker compose exec fall_detection_web sh -c 'mkdir -p /app/data/reid_crops/<gid> && cp /app/data/<bất kỳ .jpg>... '` → card hiện + ảnh (hoặc placeholder nếu chưa có file).

Expected: `/groups`=302, `/api/groups`=401, route ordered đúng (không 404). Banner hiện khi OFF.

- [ ] **Step 5: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add fall_detection_web/app.py fall_detection_web/templates/groups.html fall_detection_web/templates/index.html
git commit -m "feat(phase2): UI /groups + /api/groups + /api/reid-crop + nav (banner khi OFF)"
```

---

## Task 6: Docs — mark Phase 2 DONE (shelved)

**Files:**
- Modify: `CLAUDE.md` (bảng phase: Phase 2 → DONE shelved), spec status line.

- [ ] **Step 1: Cập nhật CLAUDE.md + spec status**

`CLAUDE.md` bảng phase: Phase 2 → `✅ DONE (shelved, OFF mặc định)` + plan link. Spec `2026-06-26-phase2-group-reid-design.md` dòng `**Trạng thái:**` → `DONE (implemented, shelved — image build + activate deferred)`. Thêm note "Phase 2 đã thêm" vào CLAUDE.md (reid_worker profile reid, 3 bảng, /groups banner-gated, build deferred x86).

- [ ] **Step 2: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add CLAUDE.md docs/superpowers/specs/2026-06-26-phase2-group-reid-design.md
git commit -m "docs(phase2): mark Group/Re-ID module DONE (shelved, OFF default)"
```

---

## Self-Review

**Spec coverage:**
- §4 Schema 3 bảng + ivfflat → Task 1 ✅
- §5a reid_worker port (6 src + 4 test + Dockerfile + requirements) + edits → Task 2 ✅
- §5b schema vào init_db (pattern Phase 1) → Task 1 ✅
- §5c db.py read funcs (reid_live_groups/group_crops/stats) → Task 4 ✅
- §5d routes /groups + /api/groups + /api/reid-crop → Task 5 ✅
- §5e groups.html → Task 5 ✅
- §5f compose service profile reid + volume → Task 3 ✅
- §5g nav link + banner → Task 5 ✅
- §7 testing (4 pure unit + smoke + path-traversal) → Task 2/4/5 ✅

**Open questions (lấy default spec — xác nhận review 2026-06-26):**
- OQ1 license: port-as-is + commercial guard + Dockerfile comment (Task 2 Step 6) ✅
- OQ2 crop purge: file-delete trong purge_loop + repo.purge_expired→list[int] (Task 2 Step 3f/4) ✅
- OQ3 rep_body drift: shelved, không fix ✅
- OQ4 pocsnap recreate: ops note (Phase 4) — không thuộc code Phase 2 ✅
- OQ5 refresh 15s: groups.html `setInterval(15000)` (Task 5 Step 2) ✅
- OQ6 duplicate on crash: accept POC, không thêm UNIQUE ✅

**Key adaptations vs DCNET (ghi rõ trong task):**
- DATA_DIR default `/data`→`/app/data` (volume khớp FDW) — Task 2 Step 3b.
- FACE_ENABLED `true`→`false` — Task 2 Step 3c.
- _dsn() prefer DATABASE_URL — Task 2 Step 3d.
- client_id `reid_worker`→`reid_worker_cameraai` — Task 2 Step 3e.
- purge_expired→list[int] + file rmtree — Task 2 Step 3f/4 (OQ2).
- INSIGHTFACE_HOME→/app/data — Task 2 Step 6.
- **Image build DEFERRED** (arm64/torch) — Task 2 KHÔNG build; Task 3 config-validate only.

**Placeholder scan:** không TODO/TBD; mọi step có code/lệnh + expected.

**Type consistency:** `reid_live_groups` trả `{id,visit_count,first_seen,last_seen,rep_crop_path}` ↔ `/api/groups` consume đúng; `reid_group_crops` trả `{kind,path,quality,ts}` ↔ map đúng; `reid_stats` `{unique_count,reentry_count}` ↔ template `uniq`/`reentry`. `purge_expired` đổi int→list[int], chỉ `purge_loop` dùng (cập nhật cùng task). crop route nhận `group_id:str` (validate isdigit) khớp URL `/api/reid-crop/{group_id}/`.

---
## Liên quan
- Spec: [phase2-group-reid-design](../specs/2026-06-26-phase2-group-reid-design.md) · Tổng thể: [migration design](../specs/2026-06-26-dcnet-platform-migration-design.md)
- Trước: [Phase 1 Đếm](2026-06-26-phase1-counting.md) (merged) · Sau: [Phase 3 Modular](../specs/2026-06-26-phase3-modular-percustomer-design.md)