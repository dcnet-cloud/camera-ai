# Session Progress Log — camera-ai

> Nhật ký liên tục giữa các phiên agent (Harness Engineering). Đọc file này + `feature_list.json` đầu mỗi phiên. Cập nhật cuối phiên.

## ⏸️ POC TẠM NGỪNG (2026-07-01)

Dự án tạm dừng ở trạng thái **ổn định, prod LIVE**. Không có feature `in-progress`. Khi resume: đọc mục "Khi resume" cuối file + `session-handoff.md`.

## Current Verified State

- **Repo root:** `/Users/vovanduc/Code/dcnet/camera-ai` (monorepo 2 app: `simple_ai_vision`, `fall_detection_web`)
- **Startup:** `./init.sh` (syntax-check 2 app + validate docker compose). Dev: `docker compose up -d` → `http://localhost:8090` (admin/admin).
- **Verification:** chưa có test suite; verify = chạy app thật + đối chứng Axis vs YOLO trên `/camera/{name}`.
- **Prod:** ✅ **LIVE** `https://camera-test.dcnet.vn` (VM `163.227.121.206`, `ssh camera`). Deploy = **rsync git-tracked files → rebuild image → up -d** (VM KHÔNG git clone được — thiếu deploy key).
- **Prod đang chạy (đo 2026-07-01):** cả 2 bộ đếm có data thật (Axis `counter` + YOLO `counter_yolo`) + thumbnail + feed sạch (overlay cam off) + timeline realtime.
- **Active feature:** KHÔNG (POC ngừng). Nhánh sạch, main = `7f22b9a`.

## Kiến trúc prod (chốt)

- **Đếm Axis** (chính): cam Axis line-crossing → MQTT `mosquitto:8883` TLS → `event_collector` (client-id `event_collector_cameraai_prod_<rand>`) → `events type=counter`. Chính xác, ~0 CPU.
- **Đếm YOLO** (đối chứng): `monitor._counting_loop` mở `rtsp://go2rtc:8554/cam_count` (720p@15) → reader thread grab-latest → `model.track` → `events type=counter_yolo`. Config per-cam ở `cameras.yolo_counting`.
- **Hạ tầng GIỮ (chung, không tắt):** `mosquitto` (cam Axis ingest — camera-ai đọc ké) + `caddy` (proxy/TLS, mosquitto mượn cert). Stack DCNET cũ + DB cũ ĐÃ decommission.
- **Camera:** 1 cam Axis M3216-LVE (`cam_uid B8A44F4627CE`, name "DCNET - Lầu 2"). go2rtc streams: `cam_door` (full-res, live+snapshot) + `cam_count` (720p@15, đếm YOLO).

## What's Done (toàn bộ)

- [x] feat-001 Phase 0 — DB Postgres (PR #1)
- [x] feat-002 Phase 1 — đếm Axis MQTT (live-proven 28/30)
- [x] feat-003 Phase 3 — modular per-cam + registry hợp nhất (PR #7)
- [x] feat-004 Phase 4 — cutover prod (2026-06-30, decommission stack cũ)
- [x] feat-005 Dual-counting UI Axis vs YOLO (PR #8/#9)
- [x] feat-006 RBAC admin/viewer + fix retry snapshot (PR #11)
- [x] feat-007 **fix độ chính xác đếm YOLO** — PR #14 merged+deployed (supersede PR #13 đã đóng)
- [x] **(session 2026-07-01)** fix Axis-thumb + YOLO-data sau cutover (PR #12); vạch YOLO khớp choke cửa; tắt overlay burn-in cam (feed sạch); grab-latest realtime (PR #14)

## What's In Progress

- KHÔNG. (POC tạm ngừng.)

## Khi resume — việc chưa đóng

1. **Đo accuracy live** (feat-007 gate còn hở): engine đã fix realtime nhưng **CHƯA đo MAE IN/OUT Axis-vs-YOLO ≥1h** trên prod. Chạy 1 config cố định, không restart, ≥1h → so tổng + xu hướng. Số ngày test cũ bẩn (nhiều restart).
2. **feat-008** (deferred): quyết có triển khai cam **non-detect** (cần YOLO thật) không → quyết định giá trị engine YOLO. Toàn cam Axis-native → YOLO chỉ là đối chứng.
3. **Ops (user tự xử):** (O6) cron backup postgres camera-ai; set `go2rtc_url`→`/live` cho live-view browser; tắt instance lạ `171.243.48.224` (connect broker sai-pass, vô hại vì prod đổi client-id).
4. **Miss người VÀO** ở cửa (YOLO): người nhỏ/xa lúc băng vạch → detect chập chờn → có thể sót hướng IN. Nếu cần: thử imgsz 960 / yolov8s (đánh đổi CPU). Vấn đề detect, không phải trễ.

## Blockers / Risks

- [ ] **Rollback prod**: KHÔNG rollback về DCNET được (DB cũ đã xoá). Mọi deploy phải verify kỹ.
- [ ] **Prod DB mới tinh**: config YOLO/AI-Vision không sang từ dev; cấu hình lại trên prod nếu resume.
- [ ] **AI Vision (§9 working notes)**: chạy được ở dev (Gemini qua 9router) nhưng prod chưa cấu hình + chưa nhập Telegram token.

## Decisions Made

- **2026-06-30**: Cam có native detect (Axis) → dùng Axis (chính xác, ~0 CPU); chỉ cam non-detect dùng YOLO. Dual-counting = công cụ test, không phải config production.
- **2026-07-01**: Vạch Axis thực ở ~y50% giữa phòng (KHÔNG ở cửa kính — ảnh "người ở cửa" do latency snapshot). Vạch YOLO canh trùng: `line_y=52, x[47,68]`.
- **2026-07-01**: Trễ YOLO = backlog frame (loop 12fps < stream 30fps) → fix grab-latest + stream đếm 720p@15 + capture-timestamp.

## Notes for Next Session

- Lịch sử tuning YOLO + AI Vision + debug prod: [`docs/2026-06-30-dual-counting-yolo-tuning.md`](docs/2026-06-30-dual-counting-yolo-tuning.md) (§11 = debug prod 2026-07-01).
- Prod access: `ssh camera`. Admin prod: `admin` / (pass user tự đặt 2026-07-01). Overlay cam rollback: VAPIX `Image.I0.Appearance.Overlays=all` (`--anyauth`).
