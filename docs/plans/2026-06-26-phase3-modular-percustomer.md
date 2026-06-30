# Phase 3: Modular per-customer (LIGHT scope) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Biến cameras (bảng `cameras`) thành **registry per-customer với module flags** (counting/fall_detection/reid/live bật-tắt độc lập) + UI quản lý toggle. Foundation cho "bán module theo khách".

**Architecture:** Thêm 4 cột boolean flag vào bảng `cameras` (Phase 1) + db helper query flag-aware + 1 trang quản lý module (SPA fetch pattern, khớp FDW hiện có) + wire flag `counting_enabled` vào trang đếm (chứng minh flag điều khiển hành vi). **Greenfield + camera sets disjoint** (Axis-ACAP đếm XOR IP-cam YOLO fall-det) → registry unification đầy đủ là elegance không phải function → DEFER.

**Tech Stack:** PostgreSQL 16 (ALTER TABLE idempotent); FastAPI + psycopg sync + Jinja2; JS SPA fetch (khớp cameras.html hiện có).

## Global Constraints

- **LIGHT scope (quyết định review 2026-06-26):** camera sets disjoint + settings-JSON cameras RỖNG (greenfield, đã verify) → KHÔNG gộp 2 registry, KHÔNG rewire monitor.py, KHÔNG đụng reid_worker (shelved). Chỉ: flag columns + db helpers + toggle UI trên bảng `cameras` + wire counting filter.
- **Schema flags vào `init_db()`** (pattern Phase 1/2, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, idempotent).
- **4 flag:** `counting_enabled`, `fall_detection_enabled`, `reid_enabled`, `live_enabled` — `BOOLEAN NOT NULL DEFAULT false`.
- **`enabled` (Phase 1) = master switch:** module chạy ⟺ `enabled=true AND <module>_enabled=true`.
- **UI = SPA fetch pattern** (GET trả JSON, POST cập nhật) — KHÔNG server-rendered form (khớp cameras.html/camera_detail.html hiện có, dùng `<dialog>`/fetch/optimistic update). Trang module-toggle thao tác trên bảng `cameras` (registry counting/reid), TÁCH BIỆT khỏi `/cameras` SPA cũ (registry fall-detection settings-JSON).
- **Counting filter:** trang đếm chỉ tính events của cam `counting_enabled=true` (JOIN cameras). Regression: Phase 1 `/counting` vẫn đúng với cam Axis (counting_enabled=true sau seed).
- **psycopg pattern:** `with get_conn() as conn: conn.execute(sql, params).fetchall()`, `%s`, `dict_row`. KHÔNG đổi hàm db.py cũ (thêm mới; counting funcs có thể sửa có kiểm soát).
- **KHÔNG Alembic** (POC, ALTER thủ công như DCNET).

### DEFERRED (ghi rõ trong spec + CLAUDE.md — wire khi có deploy mixed multi-customer thật)
- settings-JSON cameras ↔ bảng `cameras` merge (greenfield: settings-JSON rỗng, không có gì migrate; 2 registry disjoint).
- `monitor.py` rewire (`cfg["cameras"]` → DB query): monitor đọc settings-JSON cameras (rỗng) → KHÔNG chạy YOLO trên cam Axis (cam Axis ở bảng cameras, monitor không thấy) → "Axis YOLO off" goal ĐÃ đạt sẵn do registry tách biệt. Rewire chỉ cần khi gộp registry.
- `reid_worker` flag-gate (shelved, OFF; single-cam-per-container `CAM_UID` env đã là design sạch — `reid_enabled` flag là gate để check KHI activate, không sửa code shelved giờ).
- `config.py` xóa `cameras` key + per-cam config override (chỉ liên quan khi gộp registry).
- Migration script `migrate_fdw_cameras.py` (không có data để migrate).

---

## File Structure

**Modify:**
- `fall_detection_web/db.py` — `init_db()` ALTER cameras +4 flag + index + seed UPDATE Axis; thêm `list_cameras_for_module`, `list_cameras_all`, `update_camera_modules`; sửa counting queries để filter counting_enabled
- `fall_detection_web/app.py` — routes `GET /modules` (HTML), `GET /api/camera-modules` (JSON), `POST /api/camera-modules/{cam_id}` (toggle) — trước catch-all
- `fall_detection_web/templates/modules.html` (Create) — SPA toggle UI
- `fall_detection_web/templates/index.html` — nav link "Module camera"
- `docs/specs/2026-06-26-phase3-modular-percustomer-design.md` — rewrite reflect LIGHT scope + deferrals
- `CLAUDE.md` — phase table + "Phase 3 đã thêm" note

---

## Task 1: Schema flags + seed + db helpers

ALTER cameras +4 flag + index + seed UPDATE Axis cam; 3 db helper. Smoke verify.

**Files:**
- Modify: `fall_detection_web/db.py` (`init_db()` cuối; 3 hàm cuối file)

**Interfaces:**
- Consumes: `cameras` table (Phase 1), `get_conn()`.
- Produces:
  - `list_cameras_for_module(module: str) -> list[dict]` — `module ∈ {counting,fall_detection,reid,live}`; rows WHERE `enabled AND <module>_enabled`.
  - `list_cameras_all() -> list[dict]` — mọi cam + 4 flag.
  - `update_camera_modules(cam_id: int, modules: dict[str,bool]) -> None`.

- [ ] **Step 1: ALTER + index + seed trong `init_db()`**

Thêm vào CUỐI thân `with get_conn() as conn:` trong `init_db()` (sau schema Phase 2):

```python
        # ── Phase 3: module flags per-camera ──
        for col in ("counting_enabled", "fall_detection_enabled",
                    "reid_enabled", "live_enabled"):
            conn.execute(
                f"ALTER TABLE cameras ADD COLUMN IF NOT EXISTS {col} "
                "BOOLEAN NOT NULL DEFAULT false"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS cameras_fall_det "
                     "ON cameras (fall_detection_enabled) WHERE enabled = true")
        conn.execute("CREATE INDEX IF NOT EXISTS cameras_counting "
                     "ON cameras (counting_enabled) WHERE enabled = true")
        # Seed: cam Axis = đếm + live (idempotent, SET true an toàn re-run)
        conn.execute("UPDATE cameras SET counting_enabled=true, live_enabled=true "
                     "WHERE cam_uid='B8A44F4627CE'")
```

⚠️ `col` là literal whitelist trong code (KHÔNG user input) → f-string an toàn.

- [ ] **Step 2: Thêm 3 helper cuối db.py**

```python
# ── Camera module flags (Phase 3) ──

_MODULE_COLS = {"counting", "fall_detection", "reid", "live"}


def list_cameras_for_module(module: str) -> list[dict[str, Any]]:
    """Cameras đang active có module bật. module ∈ counting|fall_detection|reid|live."""
    if module not in _MODULE_COLS:
        raise ValueError(f"unknown module: {module}")
    col = f"{module}_enabled"
    sql = (f"SELECT * FROM cameras WHERE enabled = true AND {col} = true ORDER BY id")
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def list_cameras_all() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, cam_uid, name, model, location, enabled, "
            "counting_enabled, fall_detection_enabled, reid_enabled, live_enabled "
            "FROM cameras ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def update_camera_modules(cam_id: int, modules: dict[str, bool]) -> None:
    """Cập nhật flag. modules keys ⊂ {counting,fall_detection,reid,live}."""
    sets, params = [], []
    for m in _MODULE_COLS:
        if m in modules:
            sets.append(f"{m}_enabled = %s")
            params.append(bool(modules[m]))
    if not sets:
        return
    params.append(cam_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE cameras SET {', '.join(sets)} WHERE id = %s", tuple(params))
```

- [ ] **Step 3: Smoke — ALTER + helpers**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai/fall_detection_web
DATABASE_URL=postgresql://dcnet:dcnet_dev@localhost:5432/dcnet python3 -c "
import db
db.init_db()
print('all:', db.list_cameras_all())
print('counting cams:', [c['cam_uid'] for c in db.list_cameras_for_module('counting')])
print('fall_det cams:', [c['cam_uid'] for c in db.list_cameras_for_module('fall_detection')])
cam = db.list_cameras_all()[0]['id']
db.update_camera_modules(cam, {'reid': True})
print('after reid on:', db.list_cameras_for_module('reid'))
db.update_camera_modules(cam, {'reid': False})
try:
    db.list_cameras_for_module('bogus')
    print('VALIDATION FAIL')
except ValueError:
    print('module validation ok')
"
```

Expected: `all:` 1 cam có 4 flag (counting+live true, fall_det+reid false); `counting cams: ['B8A44F4627CE']`; `fall_det cams: []`; `after reid on:` 1 cam; revert; `module validation ok`.

- [ ] **Step 4: Idempotent re-run**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai/fall_detection_web
DATABASE_URL=postgresql://dcnet:dcnet_dev@localhost:5432/dcnet python3 -c "import db; db.init_db(); db.init_db(); print('idempotent ok')"
```

Expected: `idempotent ok`.

- [ ] **Step 5: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add fall_detection_web/db.py
git commit -m "feat(phase3): cameras module flags (4 bool cols) + seed Axis + db helpers"
```

---

## Task 2: Wire `counting_enabled` vào trang đếm (filter + regression)

Counting queries filter chỉ cam `counting_enabled`. Chứng minh flag điều khiển hành vi; regression Phase 1.

**Files:**
- Modify: `fall_detection_web/db.py` (`counting_occupancy_today`, `counting_crossings` — thêm filter)

**Interfaces:**
- Consumes: `events.cam_id → cameras`, flag `counting_enabled`.
- Produces: cùng signature/return shape Phase 1 (`{in,out,occupancy}`, `[{ts,direction}]`) — chỉ bớt events của cam không counting_enabled.

- [ ] **Step 1: Thêm filter `counting_enabled` vào 2 counting query**

Trong `counting_occupancy_today`: thêm điều kiện cam phải counting_enabled. Đổi `FROM events WHERE {where}` thành JOIN:

```python
    sql = (
        "SELECT "
        "COUNT(*) FILTER (WHERE e.direction = 'in')  AS ins, "
        "COUNT(*) FILTER (WHERE e.direction = 'out') AS outs "
        "FROM events e JOIN cameras c ON c.id = e.cam_id "
        f"WHERE c.enabled = true AND c.counting_enabled = true AND {where}"
    )
```

⚠️ Trong `where` đổi `type=...`/`ts...`/`cam_id=%s` prefix sang `e.` (alias) — sửa các tham chiếu cột trong `where` của hàm này thành `e.type`, `e.ts`, `e.direction`, `e.cam_id` cho khớp JOIN. Tương tự cho `counting_crossings`: `FROM events e JOIN cameras c ON c.id = e.cam_id WHERE c.enabled=true AND c.counting_enabled=true AND ...`, `SELECT e.ts, e.direction`.

(Thực hiện: đọc 2 hàm hiện tại, alias hóa `events` thành `e`, JOIN cameras `c`, thêm 2 điều kiện flag. Giữ nguyên placeholder `%s` + thứ tự params.)

- [ ] **Step 2: Smoke — counting vẫn đúng với cam counting_enabled; bằng 0 khi tắt**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai/fall_detection_web
DATABASE_URL=postgresql://dcnet:dcnet_dev@localhost:5432/dcnet python3 -c "
import db
from datetime import datetime, timezone, timedelta
db.init_db()
vn_today = datetime.now(timezone(timedelta(hours=7))).date()
# insert 1 IN event cho cam Axis (counting_enabled=true)
with db.get_conn() as c:
    cam = c.execute(\"SELECT id FROM cameras WHERE cam_uid='B8A44F4627CE'\").fetchone()['id']
    c.execute(\"INSERT INTO events (cam_id,ts,type,direction,axis_object_id,payload) VALUES (%s, now(), 'counter','in','TEST_P3', '{}'::jsonb) ON CONFLICT DO NOTHING\", (cam,))
print('occ (counting on):', db.counting_occupancy_today())
# tắt counting → phải về 0
db.update_camera_modules(cam, {'counting': False})
print('occ (counting off):', db.counting_occupancy_today())
print('crossings (off):', len(db.counting_crossings(vn_today)))
# revert
db.update_camera_modules(cam, {'counting': True})
with db.get_conn() as c:
    c.execute(\"DELETE FROM events WHERE axis_object_id='TEST_P3'\")
print('reverted')
"
```

Expected: `occ (counting on):` in≥1; `occ (counting off):` `{'in':0,'out':0,'occupancy':0}` (flag tắt → filter loại hết); `crossings (off): 0`; `reverted`.

- [ ] **Step 3: Regression — `/counting` page vẫn render**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
docker compose up -d --build fall_detection_web
sleep 4
curl -s -o /dev/null -w "/counting=%{http_code} " http://localhost:8090/counting
curl -s -o /dev/null -w "/api/counting=%{http_code}\n" http://localhost:8090/api/counting
```

Expected: `/counting=302 /api/counting=401` (auth-gated, không 500 — query JOIN không vỡ). Port :8090 nếu kẹt `fdw_demo`: `docker stop fdw_demo`.

- [ ] **Step 4: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add fall_detection_web/db.py
git commit -m "feat(phase3): counting page filter by counting_enabled flag (regression-safe)"
```

---

## Task 3: Module-toggle UI (SPA fetch) trên bảng cameras

Trang `/modules`: list cameras-table cameras + 4 checkbox/cam, toggle → POST cập nhật flag. SPA fetch pattern (khớp cameras.html). Routes trước catch-all.

**Files:**
- Modify: `fall_detection_web/app.py` (3 route trước catch-all `/{page_name}`)
- Create: `fall_detection_web/templates/modules.html`
- Modify: `fall_detection_web/templates/index.html` (nav link)

**Interfaces:**
- Consumes: `db.list_cameras_all`, `db.update_camera_modules` (Task 1); `auth.require_auth`, `templates` (app.py).
- Produces: `GET /modules` (HTML), `GET /api/camera-modules` (JSON `{cameras:[{id,cam_uid,name,enabled,counting_enabled,fall_detection_enabled,reid_enabled,live_enabled}]}`), `POST /api/camera-modules/{cam_id}` (body `{counting,fall_detection,reid,live: bool}` → update).

- [ ] **Step 1: Thêm 3 route vào app.py (TRƯỚC catch-all `/{page_name}`)**

`Body` đã import ở app.py (dùng bởi `/api/config`). Thêm:

```python
@app.get("/modules", response_class=HTMLResponse)
def modules_page(request: Request, _: str = Depends(auth.require_auth)):
    return templates.TemplateResponse(request=request, name="modules.html", context={})


@app.get("/api/camera-modules")
def api_camera_modules(_: str = Depends(auth.require_auth)):
    return {"cameras": db.list_cameras_all()}


@app.post("/api/camera-modules/{cam_id}")
def api_update_camera_modules(cam_id: int, payload: dict[str, Any] = Body(...),
                              _: str = Depends(auth.require_auth)):
    modules = {m: bool(payload.get(m, False))
               for m in ("counting", "fall_detection", "reid", "live")
               if m in payload}
    db.update_camera_modules(cam_id, modules)
    return {"ok": True, "cam_id": cam_id, "modules": modules}
```

⚠️ Xác nhận `Body` và `Any` đã import đầu app.py (đã dùng ở `/api/config`/`/api/cameras`). Nếu thiếu, thêm.

- [ ] **Step 2: Tạo `templates/modules.html`**

SPA: GET cameras → render rows + 4 checkbox; on-change POST flag của cam đó (optimistic). Self-contained, style như counting/groups.

```html
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Module camera — DCNET Camera</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.ico">
  <style>
    :root { --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8;
            --acc:#38bdf8; --on:#22c55e; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text);
           font-family:system-ui,-apple-system,sans-serif; padding:24px; }
    a { color:var(--acc); text-decoration:none; }
    h1 { font-size:1.4rem; margin:0 0 4px; }
    .cap { color:var(--muted); font-size:.85rem; margin:0 0 18px; }
    table { width:100%; border-collapse:collapse; background:var(--card); border-radius:12px; overflow:hidden; }
    th, td { padding:12px 14px; text-align:left; border-bottom:1px solid rgba(148,163,184,.15); }
    th { color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.04em; }
    td.cam { font-weight:600; }
    td .uid { color:var(--muted); font-size:.72rem; font-weight:400; }
    .mod { text-align:center; }
    input[type=checkbox] { width:18px; height:18px; accent-color:var(--on); cursor:pointer; }
    .master.off { opacity:.45; }
    .badge { font-size:.7rem; padding:1px 7px; border-radius:5px; }
    .badge.on { background:rgba(34,197,94,.2); color:var(--on); }
    .badge.off { background:rgba(148,163,184,.18); color:var(--muted); }
    #msg { margin-top:12px; font-size:.82rem; color:var(--muted); min-height:1.2em; }
  </style>
</head>
<body>
  <p><a href="/">← Dashboard</a></p>
  <h1>Module camera (per-customer)</h1>
  <p class="cap">Bật/tắt module độc lập mỗi camera. Module chạy ⟺ camera <b>enabled</b> + flag bật.
    (Registry counting/Re-ID. Camera fall-detection quản ở trang Cameras.)</p>
  <table>
    <thead><tr>
      <th>Camera</th><th>Trạng thái</th>
      <th class="mod">Đếm</th><th class="mod">Fall-det</th><th class="mod">Re-ID</th><th class="mod">Live</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div id="msg"></div>
  <script>
    const MODS = ["counting", "fall_detection", "reid", "live"];
    async function load() {
      const r = await fetch("/api/camera-modules", { credentials: "same-origin" });
      if (!r.ok) { document.getElementById("msg").textContent = "Lỗi tải dữ liệu."; return; }
      const d = await r.json();
      const rows = document.getElementById("rows");
      if (!d.cameras.length) { rows.innerHTML = '<tr><td colspan="6" style="color:var(--muted)">Chưa có camera.</td></tr>'; return; }
      rows.innerHTML = d.cameras.map(c => `
        <tr class="${c.enabled ? '' : 'master off'}" data-id="${c.id}">
          <td class="cam">${c.name}<div class="uid">${c.cam_uid}</div></td>
          <td><span class="badge ${c.enabled ? 'on' : 'off'}">${c.enabled ? 'ENABLED' : 'DISABLED'}</span></td>
          ${MODS.map(m => `<td class="mod"><input type="checkbox" data-mod="${m}" ${c[m + "_enabled"] ? "checked" : ""}></td>`).join("")}
        </tr>`).join("");
      rows.querySelectorAll('input[type=checkbox]').forEach(cb => {
        cb.addEventListener("change", () => save(cb.closest("tr")));
      });
    }
    async function save(tr) {
      const id = tr.dataset.id;
      const body = {};
      tr.querySelectorAll('input[type=checkbox]').forEach(cb => { body[cb.dataset.mod] = cb.checked; });
      const msg = document.getElementById("msg");
      try {
        const r = await fetch(`/api/camera-modules/${id}`, {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        msg.textContent = r.ok ? `Đã lưu camera #${id} lúc ${new Date().toLocaleTimeString()}` : "Lỗi lưu.";
      } catch (e) { msg.textContent = "Lỗi mạng khi lưu."; }
    }
    load();
  </script>
</body>
</html>
```

- [ ] **Step 3: Nav link vào index.html**

Trong `fall_detection_web/templates/index.html` nav block, thêm sau link "Nhóm theo người" (Phase 2):

```html
        <a class="nav-btn" href="/modules"><span data-icon="sliders"></span>Module camera</a>
```

- [ ] **Step 4: Verify — routes + toggle persist**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
docker compose up -d --build fall_detection_web
sleep 4
curl -s -o /dev/null -w "/modules=%{http_code} " http://localhost:8090/modules
curl -s -o /dev/null -w "/api/camera-modules=%{http_code}\n" http://localhost:8090/api/camera-modules
```

Expected: `/modules=302 /api/camera-modules=401` (auth-gated, route tồn tại trước catch-all — KHÔNG 404). Sau login (browser): `/modules` hiện bảng 1 cam Axis, checkbox Đếm+Live checked, Fall-det+Re-ID unchecked; tick Re-ID → "Đã lưu"; reload → Re-ID vẫn checked (persist DB); untick để revert.

- [ ] **Step 5: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add fall_detection_web/app.py fall_detection_web/templates/modules.html fall_detection_web/templates/index.html
git commit -m "feat(phase3): module-toggle UI /modules + /api/camera-modules (SPA, cameras table)"
```

---

## Task 4: Docs — rewrite spec (LIGHT scope) + CLAUDE.md + mark DONE

Spec hiện stale (viết blind). Rewrite phản ánh thực tế: LIGHT scope, disjoint, greenfield, deferrals. CLAUDE.md phase table + note.

**Files:**
- Modify: `docs/specs/2026-06-26-phase3-modular-percustomer-design.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite spec để khớp thực tế**

Cập nhật spec `2026-06-26-phase3-modular-percustomer-design.md`:
- Trạng thái → `DONE (implemented, LIGHT scope — plan ../plans/2026-06-26-phase3-modular-percustomer.md)`.
- Thêm section đầu "## Thực tế (audit 2026-06-26)" ghi 5 finding: (1) 2 registry disjoint schema (settings-JSON 13 field per-cam vs cameras table lean); (2) FDW cameras UI = JS SPA không phải form; (3) reid_worker = single-cam CAM_UID env (không list-query); (4) settings-JSON cameras RỖNG (greenfield); (5) "Axis YOLO off" đã đạt sẵn (monitor đọc settings-JSON rỗng, cam Axis ở cameras table).
- Đánh dấu các section §2.4/§2.5/§4.3 (migration settings→table), §5.b (monitor rewire), §5.c (config.py xóa cameras), §5.g (reid_worker wire), §5.h (migration script) là **DEFERRED** (camera sets disjoint + greenfield → wire khi có deploy mixed multi-customer thật).
- Ghi: implemented = flag columns + db helpers + counting filter + module-toggle UI (`/modules`).

- [ ] **Step 2: CLAUDE.md phase table + note**

- Bảng phase: Phase 3 → `✅ DONE (LIGHT scope; full registry-merge deferred)` + plan link.
- Thêm "**Phase 3 đã thêm:**" note: 4 flag column (`counting/fall_detection/reid/live_enabled`) trên bảng `cameras` + `enabled` master switch; db helpers `list_cameras_for_module`/`list_cameras_all`/`update_camera_modules`; counting page filter `counting_enabled`; trang `/modules` toggle UI (SPA, cameras table). ⚠️ **DEFERRED (disjoint+greenfield):** settings-JSON↔cameras-table merge, monitor.py rewire, reid_worker flag-gate, config.py cameras cleanup — wire khi có deploy mixed multi-customer thật.

- [ ] **Step 3: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add CLAUDE.md docs/specs/2026-06-26-phase3-modular-percustomer-design.md
git commit -m "docs(phase3): rewrite spec to LIGHT scope (audit reality) + mark DONE"
```

---

## Self-Review

**Scope (LIGHT, review 2026-06-26):** disjoint camera sets + greenfield → implement registry+flags+UI, defer service-wiring đòi registry merge.
- Flag columns + seed + db helpers → Task 1 ✅
- counting_enabled wire (1 flag điều khiển hành vi, regression-safe) → Task 2 ✅
- Module-toggle UI (SPA, product-vision deliverable) → Task 3 ✅
- Spec rewrite (khớp reality, đánh dấu deferrals) + CLAUDE.md → Task 4 ✅

**Deferred (documented Task 4):** settings-JSON↔table merge, monitor.py rewire, reid_worker gate, config.py cameras cleanup, migration script. Lý do: disjoint + greenfield (settings-JSON rỗng, đã verify) → no data, "Axis YOLO off" đã đạt sẵn.

**Adaptations vs spec gốc (spec viết blind):**
- UI = SPA fetch (KHÔNG server-rendered `POST /cameras/{id}/modules` form) — khớp cameras.html.
- KHÔNG đụng reid_worker (shelved, single-cam design sạch).
- Module UI tách trang `/modules` (cameras table) khỏi `/cameras` SPA (settings-JSON fall-det registry) — 2 registry vẫn tách (acceptable, disjoint).

**Placeholder scan:** không TODO/TBD; mọi step có code/lệnh + expected. Task 2 Step 1 mô tả alias-hóa (đọc 2 hàm hiện tại + JOIN) — implementer cần đọc db.py counting funcs hiện tại để alias chính xác.

**Type consistency:** `list_cameras_all` trả keys (id,cam_uid,name,...,4 flag) ↔ modules.html JS đọc `c[m+"_enabled"]` + `c.enabled` ✅. `update_camera_modules(cam_id, {module:bool})` ↔ POST body keys `counting/fall_detection/reid/live` ✅. `list_cameras_for_module(module)` validate ∈ _MODULE_COLS ✅. counting funcs giữ return shape Phase 1 ✅.

---
## Liên quan
- Spec: [phase3-modular-percustomer-design](../specs/2026-06-26-phase3-modular-percustomer-design.md) · Tổng thể: [migration design](../specs/2026-06-26-dcnet-platform-migration-design.md)
- Trước: [Phase 2 Group/Re-ID](2026-06-26-phase2-group-reid.md) (merged) · Sau: [Phase 4 Deploy](../specs/2026-06-26-phase4-deploy-cutover-design.md)