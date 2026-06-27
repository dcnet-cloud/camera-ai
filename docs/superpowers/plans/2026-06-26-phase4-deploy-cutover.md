# Phase 4: Deploy / Cutover — Implementation Plan (PREP-ONLY, no VM execution)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Soạn + verify (local) mọi **artifact deploy** để cutover camera-ai thay Streamlit trên prod `camera-test.dcnet.vn` — KHÔNG ssh/deploy trong plan này. Actual VM ops = session riêng qua `dcnet-deploy` skill, theo runbook produced ở đây.

**Architecture:** camera-ai chạy song song stack DCNET cũ trên VM, parity-verify, flip Caddy upstream `dashboard:8501→fall_detection_web:8090`, decommission cũ. Mosquitto = shared infra (KHÔNG tắt). Broker đọc-ké client-id riêng (như dev).

**Tech Stack:** docker-compose (prod overlay); Postgres 16 pgvector; FastAPI/Jinja; go2rtc (RTSP→WS); Caddy (tái dùng từ stack DCNET); forward_auth → FDW JWT.

## Global Constraints

- **PREP-ONLY:** plan này CHỈ tạo + verify artifact local (compose.prod, init.sql, go2rtc.yaml, auth-check endpoint, Caddyfile draft, runbook). KHÔNG `docker compose up` prod, KHÔNG ssh VM, KHÔNG flip. Deploy = dcnet-deploy session sau.
- **Mosquitto = shared infra:** compose.prod OMIT mosquitto. `event_collector` đọc-ké cloud broker TLS `camera-test.dcnet.vn:8883`, `MQTT_CLIENT_ID=event_collector_cameraai`.
- **Caddy = extend không replace:** KHÔNG khai báo Caddy trong compose.prod (tái dùng Caddy stack DCNET). Caddyfile post-flip = DRAFT doc (file thật sống trên VM ở repo DCNET).
- **Security invariant (post-flip):** bỏ Caddy `basic_auth` site-wide → MỌI route phải JWT-gated (FDW) hoặc forward_auth. Không bare `reverse_proxy` không auth. `/live/*` + `/cam/*` cần auth gate (O9) TRƯỚC flip.
- **Schema race fix:** thêm `db/init.sql` (dump schema Phase 0-3) mount vào postgres init → schema có trước mọi service. (Defense-in-depth: collector đã có `ensure_schema()` từ Phase 1, nhưng init.sql là pattern prod sạch hơn.)
- **Ports:** prod KHÔNG publish host port cho postgres/fdw/go2rtc/event_collector — chỉ qua docker network. ufw GIỮ NGUYÊN `[22,80,443,8883]`.
- **Greenfield parity:** camera-ai client-id mới → không nhận backlog. Parity = so IN/OUT/occupancy trong cửa sổ chung (incremental forward).
- **reid_worker:** `profiles: [reid]` (off mặc định).
- **VM-dependent Opens (O1-O9):** KHÔNG resolve được trong plan (cần ssh). Mỗi cái → ghi vào runbook "resolve-at-deploy" checklist với lệnh kiểm tra cụ thể.

---

## File Structure

**Create:**
- `db/init.sql` — schema dump Phase 0-3 (postgres init mount)
- `docker-compose.prod.yml` — prod overlay (camera-ai root)
- `go2rtc.yaml` — RTSP→WS config (creds từ env)
- `.env.example` — prod env template (placeholders, KHÔNG secrets)
- `docs/ops/2026-06-26-phase4-cutover-runbook.md` — runbook A-D + resolve-at-deploy + verification + rollback
- `docs/ops/Caddyfile.post-flip.draft` — draft Caddyfile (deploy-time reference)

**Modify:**
- `fall_detection_web/app.py` — `GET /api/auth/check` (forward_auth target, JWT-gated) trước catch-all
- `docs/superpowers/specs/2026-06-26-phase4-deploy-cutover-design.md` — status
- `CLAUDE.md` — phase table + note

---

## Task 1: `db/init.sql` — schema dump Phase 0-3 (race fix)

Dump schema hiện tại (đã có đủ Phase 0-3 trong dev DB) → `db/init.sql`, mount-ready cho postgres init. Verify apply lên postgres trắng.

**Files:**
- Create: `db/init.sql`

- [ ] **Step 1: Dump schema từ dev postgres**

Dev postgres đang chạy + `init_db()` đã tạo đủ bảng (incidents/users/settings/cameras/events/person_group/appearance/appearance_crop + flag cols). Dump schema-only:

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
mkdir -p db
docker compose exec -T postgres pg_dump -U dcnet -d dcnet \
  --schema-only --no-owner --no-privileges \
  > db/init.sql
echo "=== tables in dump ===" && grep -E '^CREATE TABLE' db/init.sql
echo "=== vector + ivfflat present? ===" && grep -cE 'vector\(512\)|ivfflat|CREATE EXTENSION' db/init.sql
```

Expected: `CREATE TABLE` cho đủ 8 bảng (incidents, users, settings, cameras, events, person_group, appearance, appearance_crop); vector/ivfflat/extension present (≥3).

- [ ] **Step 2: Prepend `CREATE EXTENSION` guard (nếu pg_dump không đặt đầu)**

pg_dump thường đặt `CREATE EXTENSION vector` đúng chỗ, nhưng đảm bảo nó chạy TRƯỚC bảng dùng `vector(512)`. Kiểm tra: nếu dòng `CREATE EXTENSION ... vector` xuất hiện SAU `CREATE TABLE person_group` trong file, di chuyển lên đầu (sau header). Nếu đã ở đầu, bỏ qua. (pg_dump 16 thường sắp đúng thứ tự dependency.)

Verify thứ tự:
```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
grep -nE 'CREATE EXTENSION.*vector|CREATE TABLE.*person_group|CREATE TABLE person_group' db/init.sql | head
```
Expected: dòng `EXTENSION ... vector` có số dòng NHỎ HƠN dòng `person_group`. Nếu không → sửa tay đưa CREATE EXTENSION lên trước.

- [ ] **Step 3: Verify apply lên postgres trắng (throwaway container)**

```bash
docker run --rm -d --name pg_p4test -e POSTGRES_USER=dcnet -e POSTGRES_PASSWORD=t \
  -e POSTGRES_DB=dcnet -v /Users/vovanduc/Code/dcnet/camera-ai/db/init.sql:/docker-entrypoint-initdb.d/01_init.sql:ro \
  pgvector/pgvector:pg16
sleep 8
docker exec pg_p4test psql -U dcnet -d dcnet -tA -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
docker exec pg_p4test psql -U dcnet -d dcnet -tA -c \
  "SELECT 1 FROM information_schema.columns WHERE table_name='cameras' AND column_name='counting_enabled';"
docker logs pg_p4test 2>&1 | grep -iE 'error|fatal' | grep -v 'already exists' || echo "no init errors"
docker stop pg_p4test
```

Expected: table count ≥ 8; `counting_enabled` column = `1`; no init errors (init.sql chạy clean trong postgres bootstrap → chứng minh schema có trước app, fix race).

- [ ] **Step 4: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add db/init.sql
git commit -m "feat(phase4): db/init.sql schema dump (Phase 0-3) for postgres-init (race fix)"
```

---

## Task 2: `docker-compose.prod.yml` + `.env.example`

Prod overlay: postgres (no-publish + init.sql mount) + fdw + event_collector + go2rtc + reid_worker(profile) + dcnet-shared external net + restart. Verify `config`.

**Files:**
- Create: `docker-compose.prod.yml`, `.env.example`

**Interfaces:**
- Consumes: `db/init.sql` (Task 1), `go2rtc.yaml` (Task 3), images build từ `./fall_detection_web` + `./services/*`.

- [ ] **Step 1: Tạo `.env.example` (placeholders, gitignore-safe)**

Create `.env.example`:
```
# camera-ai prod env (copy → .env, điền giá trị thật, KHÔNG commit .env)
DB_PASSWORD=changeme_db
SECRET_KEY=changeme_random_32bytes
JWT_SECRET_KEY=changeme_random_32bytes
# Cloud broker (đọc-ké, client-id riêng)
MQTT_HOST=camera-test.dcnet.vn
MQTT_PORT=8883
MQTT_TLS=true
MQTT_USER=dcnet
MQTT_PASSWORD=changeme_mqtt
MQTT_CLIENT_ID=event_collector_cameraai
# Cam RTSP cho go2rtc live view (cam NAT public)
CAM_USER=changeme
CAM_PASS=changeme
CAM_RTSP_HOST=115.79.47.96
CAM_RTSP_PORT=554
```

- [ ] **Step 2: Tạo `docker-compose.prod.yml`**

Create `docker-compose.prod.yml`:
```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: dcnet
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: dcnet
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/01_init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U dcnet -d dcnet"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped
    # KHÔNG publish port — internal only

  fall_detection_web:
    build: ./fall_detection_web
    env_file:
      - path: ./.env
        required: false
    environment:
      DATABASE_URL: postgresql://dcnet:${DB_PASSWORD}@postgres:5432/dcnet
      SECRET_KEY: ${SECRET_KEY}
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
    volumes:
      - fdw_data:/app/data
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
    networks: [default, dcnet-shared]
    # KHÔNG publish port — Caddy reach qua dcnet-shared

  event_collector:
    build: ./services/event_collector
    env_file:
      - path: ./.env
        required: false
    environment:
      DATABASE_URL: postgresql://dcnet:${DB_PASSWORD}@postgres:5432/dcnet
      MQTT_HOST: ${MQTT_HOST:-camera-test.dcnet.vn}
      MQTT_PORT: ${MQTT_PORT:-8883}
      MQTT_TLS: ${MQTT_TLS:-true}
      MQTT_USER: ${MQTT_USER:-}
      MQTT_PASSWORD: ${MQTT_PASSWORD:-}
      MQTT_CLIENT_ID: ${MQTT_CLIENT_ID:-event_collector_cameraai}
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

  go2rtc:
    image: alexxit/go2rtc:latest
    env_file:
      - path: ./.env
        required: false
    volumes:
      - ./go2rtc.yaml:/config/go2rtc.yaml:ro
      - go2rtc_data:/config
    restart: unless-stopped
    networks: [default, dcnet-shared]
    # Expose 1984 nội bộ; Caddy /live/* reach qua dcnet-shared. KHÔNG publish host port.

  reid_worker:
    build: ./services/reid_worker
    profiles: ["reid"]
    env_file:
      - path: ./.env
        required: false
    environment:
      DATABASE_URL: postgresql://dcnet:${DB_PASSWORD}@postgres:5432/dcnet
      MQTT_CLIENT_ID: reid_worker_cameraai
      REID_FACE_ENABLED: "false"
      REID_TOPIC: poc/objsnap
      DATA_DIR: /app/data
    volumes:
      - fdw_data:/app/data
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

volumes:
  pgdata:
  fdw_data:
  go2rtc_data:

networks:
  default:
    name: camera-ai_default
  dcnet-shared:
    external: true   # pre-create trên VM: docker network create dcnet-shared
```

- [ ] **Step 3: Verify compose config (KHÔNG up)**

Cần `dcnet-shared` external network tồn tại để `config` validate; tạo tạm local rồi xóa:
```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
docker network create dcnet-shared 2>/dev/null || true
# tạo .env tạm từ example để config interpolate (nếu chưa có .env thật)
[ -f .env ] || cp .env.example .env.p4tmp
DOTENV=$([ -f .env ] && echo .env || echo .env.p4tmp)
docker compose -f docker-compose.prod.yml --env-file "$DOTENV" config >/dev/null && echo "prod compose valid"
docker compose -f docker-compose.prod.yml --env-file "$DOTENV" config --services
echo "=== reid in default? ===" && docker compose -f docker-compose.prod.yml --env-file "$DOTENV" config --services | grep -q reid_worker && echo "WRONG(in default)" || echo "reid profile-gated OK"
echo "=== no host ports published? ===" && docker compose -f docker-compose.prod.yml --env-file "$DOTENV" config | grep -A2 'published:' || echo "no published host ports (correct)"
rm -f .env.p4tmp
```

Expected: `prod compose valid`; services = postgres, fall_detection_web, event_collector, go2rtc (NOT reid_worker in default); `reid profile-gated OK`; `no published host ports`.

- [ ] **Step 4: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add docker-compose.prod.yml .env.example
git commit -m "feat(phase4): docker-compose.prod.yml (no-publish, go2rtc, reid profile, dcnet-shared) + .env.example"
```

---

## Task 3: go2rtc config + FDW `/api/auth/check` (forward_auth target — O9)

`go2rtc.yaml` (RTSP từ env) + endpoint forward_auth để Caddy gate `/live/*` + `/cam/*` (giải O9). Verify endpoint 200/401 local.

**Files:**
- Create: `go2rtc.yaml`
- Modify: `fall_detection_web/app.py` (`GET /api/auth/check` trước catch-all)

**Interfaces:**
- Consumes: `auth.require_auth` (JWT cookie). Produces: `GET /api/auth/check` → 200 nếu JWT hợp lệ, 401 nếu không (Caddy forward_auth dùng).

- [ ] **Step 1: Tạo `go2rtc.yaml`**

Create `go2rtc.yaml` (creds qua env var go2rtc expand `${...}`):
```yaml
streams:
  cam_door:
    - rtsp://${CAM_USER}:${CAM_PASS}@${CAM_RTSP_HOST}:${CAM_RTSP_PORT}/axis-media/media.amp

api:
  listen: ":1984"

log:
  level: info
```
⚠️ go2rtc hỗ trợ `${ENV}` expansion trong config. Stream `cam_door` reach qua WebSocket `/api/ws?src=cam_door`. (Verify RTSP 554 reach từ VM = deploy-time O4, ghi runbook.)

- [ ] **Step 2: Thêm `GET /api/auth/check` vào app.py (TRƯỚC catch-all `/{page_name}`)**

```python
@app.get("/api/auth/check")
def auth_check(_: str = Depends(auth.require_auth)):
    # Caddy forward_auth target: 200 nếu session JWT hợp lệ, 401 nếu không.
    # Dùng để gate /live/* (go2rtc) + /cam/* sau khi bỏ Caddy basic_auth (Phase 4 flip).
    return {"ok": True}
```

- [ ] **Step 3: Verify endpoint 200 (authed) / 401 (no cookie)**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
docker compose up -d --build fall_detection_web
sleep 4
echo "=== no cookie → 401 ===" && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8090/api/auth/check
echo "=== login → cookie → 200 ===" 
TOKEN=$(curl -s -c /tmp/p4cookie.txt -X POST http://localhost:8090/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin" -o /dev/null -w "%{http_code}")
echo "login status: $TOKEN"
curl -s -b /tmp/p4cookie.txt -o /dev/null -w "with cookie: %{http_code}\n" http://localhost:8090/api/auth/check
rm -f /tmp/p4cookie.txt
```

Expected: no cookie → `401`; login → `200` or `302`; with cookie → `200`. (Nếu admin pwd đã đổi ở dev DB, dùng pwd đúng — hoặc reset. Mục tiêu: 401 khi không auth, 200 khi auth.)

- [ ] **Step 4: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add go2rtc.yaml fall_detection_web/app.py
git commit -m "feat(phase4): go2rtc.yaml + /api/auth/check (forward_auth target for /live + /cam gate)"
```

---

## Task 4: Caddyfile post-flip draft + cutover runbook

Draft Caddyfile (deploy reference) + runbook đầy đủ (A-D + resolve-at-deploy O1-O9 + verification + rollback). Doc artifacts — KHÔNG execute.

**Files:**
- Create: `docs/ops/Caddyfile.post-flip.draft`, `docs/ops/2026-06-26-phase4-cutover-runbook.md`

- [ ] **Step 1: Tạo `docs/ops/Caddyfile.post-flip.draft`**

Create `docs/ops/Caddyfile.post-flip.draft` (draft — file thật sống trên VM repo DCNET):
```caddyfile
# camera-ai POST-FLIP — thay site block hiện tại (dashboard → FDW)
# ⚠️ basic_auth ĐÃ BỎ site-wide → mọi route phải JWT-gated hoặc forward_auth.
camera-test.dcnet.vn {
    encode zstd gzip

    # Live view (go2rtc WS) — gate qua FDW JWT trước khi forward
    handle /live/* {
        forward_auth fall_detection_web:8090 {
            uri /api/auth/check
            copy_headers Cookie
        }
        reverse_proxy go2rtc:1984 {
            header_up Upgrade {http.request.header.Upgrade}
            header_up Connection {http.request.header.Connection}
        }
    }

    # Cam snapshot/mjpeg fallback (chỉ nếu go2rtc chưa verified — §2.7)
    # handle /cam/* {
    #     forward_auth fall_detection_web:8090 { uri /api/auth/check; copy_headers Cookie }
    #     reverse_proxy cam_proxy:80
    # }

    handle {
        reverse_proxy fall_detection_web:8090
    }
}
# Mosquitto cert-sync logic KHÔNG đổi (broker 8883 mượn cert Caddy).
```

- [ ] **Step 2: Tạo runbook `docs/ops/2026-06-26-phase4-cutover-runbook.md`**

Create với nội dung (port từ spec §5 + §10 + §12 + §7):
- **Resolve-at-deploy checklist (O1-O9)** — mỗi Open + lệnh kiểm trên VM: O1 `/opt/camera-ai`? ; O2 `docker compose ls` (tên project → network prefix); O3 `free -h && df -h` (RAM/disk 2× postgres); O4 `nc -zv 115.79.47.96 554` (RTSP reach); O5 go2rtc image version pin; O6 backup cron; O8 thêm user (no /admin/users → INSERT bcrypt hoặc dùng admin); O9 → đã giải (forward_auth + /api/auth/check, Task 3).
- **Giai đoạn A (chuẩn bị):** clone /opt/camera-ai, `.env` từ example (điền secrets), `docker network create dcnet-shared`, connect Caddy DCNET vào dcnet-shared, `docker compose -f docker-compose.prod.yml up -d --build`, đổi admin pwd qua UI TRƯỚC flip.
- **Giai đoạn B (parity ≥1 ngày):** verify collector INSERT; staging route `/staging/*`; parity query (so events count DCNET vs camera-ai cửa sổ chung); pass criteria (delta IN ≤2/4h, no systematic drift, occupancy chỉ so nếu collector live trước 00:00).
- **Giai đoạn C (flip):** sửa Caddyfile theo draft (bỏ basic_auth + forward_auth /live), `caddy reload`, smoke test.
- **Giai đoạn D (decommission ≥1-3 ngày):** stop dashboard → stop event_collector DCNET → giữ mosquitto + postgres DCNET ≥7 ngày.
- **Verification (§12):** 8 check, đặc biệt #6 auth-gate (`curl /api/counting` no-token → 401; bất kỳ 200 không cred = block).
- **Rollback (§7):** flip Caddy về dashboard:8501 + restore basic_auth; old stack chưa tắt → instant.
- **Requirements reconcile note:** prod x86 → FDW image build dùng `requirements.txt` (+cpu wheels, --extra-index-url whl/cpu — hợp lệ x86 linux, image nhỏ) thay vì `requirements.docker.txt` (plain torch CUDA). Quyết định build-arg/đổi Dockerfile COPY tại deploy (verify build trên x86, không verify được arm64 dev).
- **Risks (§11):** mosquitto tắt nhầm (stop selective), schema race (init.sql Task 1 + collector ensure_schema), auth leak (verify #6).

(Soạn runbook đầy đủ các mục trên — markdown, mỗi giai đoạn lệnh cụ thể từ spec.)

- [ ] **Step 3: Verify runbook + draft hợp lệ (lint nhẹ)**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
ls -la docs/ops/Caddyfile.post-flip.draft docs/ops/2026-06-26-phase4-cutover-runbook.md
echo "=== runbook có đủ giai đoạn? ===" && grep -cE 'Giai đoạn A|Giai đoạn B|Giai đoạn C|Giai đoạn D|Resolve-at-deploy|Rollback|Verification' docs/ops/2026-06-26-phase4-cutover-runbook.md
echo "=== forward_auth /api/auth/check trong draft? ===" && grep -c 'api/auth/check' docs/ops/Caddyfile.post-flip.draft
```

Expected: cả 2 file tồn tại; runbook match ≥7 (đủ section); draft có `/api/auth/check` (≥1).

- [ ] **Step 4: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add docs/ops/Caddyfile.post-flip.draft docs/ops/2026-06-26-phase4-cutover-runbook.md
git commit -m "docs(phase4): Caddyfile post-flip draft + cutover runbook (deploy-time)"
```

---

## Task 5: Docs — spec status + CLAUDE.md

**Files:**
- Modify: spec status; CLAUDE.md phase table + note.

- [ ] **Step 1: Spec status + CLAUDE.md**

- Spec `2026-06-26-phase4-deploy-cutover-design.md` Trạng thái → `PREP DONE (artifacts + runbook; VM cutover pending dcnet-deploy session)`.
- CLAUDE.md phase table: Phase 4 → `🟡 PREP DONE (artifacts ready; deploy pending)` + plan link.
- CLAUDE.md note "Phase 4 đã chuẩn bị": `docker-compose.prod.yml` + `db/init.sql` (schema race fix) + `go2rtc.yaml` + `/api/auth/check` (forward_auth O9) + Caddyfile draft + cutover runbook (`docs/ops/`). Deploy = dcnet-deploy session: clone /opt/camera-ai, dcnet-shared net, up prod, parity ≥1 ngày, flip Caddy, decommission. VM-Opens (O1-O9) resolve-at-deploy trong runbook. ⚠️ requirements x86 reconcile note trong runbook.

- [ ] **Step 2: Commit**

```bash
cd /Users/vovanduc/Code/dcnet/camera-ai
git add CLAUDE.md docs/superpowers/specs/2026-06-26-phase4-deploy-cutover-design.md
git commit -m "docs(phase4): mark PREP DONE (deploy artifacts ready, VM cutover pending)"
```

---

## Self-Review

**Spec coverage (PREP-ONLY interpretation):**
- §4.2 schema race → `db/init.sql` Task 1 ✅
- §4 docker-compose.prod.yml → Task 2 ✅
- §2.7 go2rtc config → Task 3 ✅
- §2.4/O9 auth gate `/live`+`/cam` → `/api/auth/check` forward_auth Task 3 ✅
- §5 cutover procedure (A-D) → runbook Task 4 ✅
- §2.2 Caddy flip draft → Caddyfile.post-flip.draft Task 4 ✅
- §10 Opens O1-O9 → resolve-at-deploy checklist trong runbook Task 4 ✅
- §7 rollback, §12 verification → runbook Task 4 ✅

**PREP-ONLY boundary (KHÔNG trong plan này — runbook cho dcnet-deploy):** clone VM, .env secrets, dcnet-shared create, up prod, parity run, Caddy flip, decommission. Mọi ssh/prod op.

**Adaptations vs spec:**
- O9 giải = forward_auth + FDW `/api/auth/check` (verifiable local) — không phải FDW-proxy websocket (phức tạp hơn).
- db/init.sql = pg_dump schema-only (deterministic) + defense-in-depth với collector ensure_schema (Phase 1).
- requirements x86 reconcile = documented decision trong runbook (build-verify chỉ ở deploy x86, arm64 không build được).

**VM-dependent Opens KHÔNG resolve (cần ssh):** O1(dir) O2(project name) O3(RAM) O4(RTSP 554) O5(go2rtc version) O6(cron) O8(user) — tất cả → runbook checklist với lệnh kiểm.

**Placeholder scan:** Task 4 Step 2 runbook nội dung mô tả mục cần soạn (port từ spec §5/§10/§12) — implementer soạn markdown đầy đủ từ spec; KHÔNG phải code step nên không cần code block. Các task khác có code/lệnh + expected cụ thể.

**Type consistency:** `/api/auth/check` JWT-gated ↔ Caddyfile draft `forward_auth uri /api/auth/check` ✅. compose.prod env (DATABASE_URL/MQTT_*) ↔ .env.example keys ✅. go2rtc.yaml `${CAM_*}` ↔ .env.example CAM_* ✅. db/init.sql ↔ compose postgres mount path ✅.

---
## Liên quan
- Spec: [phase4-deploy-cutover-design](../specs/2026-06-26-phase4-deploy-cutover-design.md) · Tổng thể: [migration design](../specs/2026-06-26-dcnet-platform-migration-design.md)
- Trước: [Phase 3 Modular](2026-06-26-phase3-modular-percustomer.md) (merged) · Deploy: skill `dcnet-deploy` (session riêng, theo runbook)