# Session Progress Log — camera-ai

> Nhật ký liên tục giữa các phiên agent (Harness Engineering). Đọc file này + `feature_list.json` đầu mỗi phiên. Cập nhật cuối phiên.

## Current Verified State

- **Repo root:** `/Users/vovanduc/Code/dcnet/camera-ai` (monorepo 2 app: `simple_ai_vision`, `fall_detection_web`)
- **Startup path:** `./init.sh` (syntax-check 2 app + validate docker compose) — xem [`AGENTS`/CLAUDE.md] để chi tiết run từng app
- **Verification path:** chưa có test suite; verify = chạy app thật + (đếm) đối chứng Axis vs YOLO trên `/camera/{name}`
- **Prod:** ✅ LIVE `https://camera-test.dcnet.vn` (VM `163.227.121.206`, `ssh -i ~/.ssh/id_ed25519_flow_demo ubuntu@163.227.121.206`). Deploy = **rsync git-tracked files → rebuild image → up -d** (KHÔNG git trên VM)
- **Active feature:** `feat-007` — Sửa độ chính xác đếm YOLO
- **Current blocker:** `feat-008` chờ user quyết có triển khai cam non-detect (cần YOLO) hay không

## What's Done (gần đây)

- [x] feat-006: RBAC admin/viewer + fix retry snapshot — **deployed prod 2026-06-30** (commit `593f9d6`)
- [x] Backport event_collector snapshot vol/env vào `docker-compose.prod.yml` (main↔prod khớp)
- [x] feat-007 **root-cause analysis** xong (chưa fix)
- [x] Áp Harness Engineering (feature_list/progress/init/handoff + section CLAUDE.md) + migrate `docs/superpowers/{specs,plans}` → `docs/{specs,plans}` (skill `dcnet-workflow` global hoá quy trình)
- [x] **(qua đêm 2026-06-30)** feat-007 CODE: implement threaded reader trên nhánh `feat/yolo-counting-thread-fix` (spec+plan trong docs/specs|plans) → verify cơ chế (probe prod: backlog 0) → **PR mở, CHỜ user review/merge**. KHÔNG deploy.

## What's In Progress

- [ ] feat-007 — **fix engine đếm YOLO** (CODE XONG trên nhánh, chờ duyệt + đo live)
  - Root cause (đo 2026-06-30): `_counting_loop` xử lý 9.8 FPS < nguồn 17 FPS → `CAP_PROP_BUFFERSIZE=1` bị FFMPEG bỏ qua → backlog frame cũ tăng vô hạn (8.5s) → ByteTrack đứt track ID → sót lượt (tệ nhất IN).
  - **FIX đã code** (nhánh `feat/yolo-counting-thread-fix`): `_LatestFrameGrabber` (thread giữ frame mới nhất 1-slot) + `_counting_loop` xử lý theo seq mới → loop luôn realtime. Verify probe prod read-only: backlog=0, skip stale không queue, lag ~0.4 frame. `init.sh` OK.
  - **CHỜ (gate đóng feat-007):** (1) user chốt feat-008; (2) deploy: rebuild image + đổi `rtsp_url`→substream cam thật + đo accuracy live Axis vs YOLO ≥1h.

## What's Next

1. **User review PR** `feat/yolo-counting-thread-fix` + **chốt feat-008** (có cam non-detect cần YOLO không).
2. Nếu merge: deploy (rebuild image + đổi `rtsp_url` substream) → đối chứng Axis vs YOLO ≥1h → đo MAE IN/OUT → đóng feat-007.
3. Nếu toàn cam Axis (feat-008=không): có thể không merge; chỉ tắt `yolo_counting`, dùng Axis.

## Blockers / Risks

- [ ] **feat-008 blocker**: chưa rõ có triển khai cam non-detect → có thể không cần fix engine cho cam Axis (chỉ tắt YOLO, dùng Axis).
- [ ] **Rollback prod**: KHÔNG rollback về DCNET được (DB cũ đã xoá). Mọi deploy phải verify kỹ.
- [ ] **Prod DB mới tinh**: config YOLO dual-counting không sang từ dev; cấu hình lại trên prod nếu cần.

## Decisions Made

- **2026-06-30**: Chiến lược production — cam có native detect dùng Axis (chính xác, ~0 CPU); chỉ cam non-detect dùng YOLO. Dual-counting là công cụ test, không phải config production.
- **2026-06-30**: Nguồn lệch Axis/YOLO KHÔNG do "bật cả 2 counter" (Axis độc lập ~0 CPU) mà do bug throughput engine YOLO + YOLO #2 (fall_detection) giành CPU.

## Notes for Next Session

Đọc `docs/2026-06-30-dual-counting-yolo-tuning.md` để biết lịch sử tuning YOLO. Root-cause feat-007 đã đo xong — vào thẳng implement khi user chốt feat-008. Prod access: `ssh -i ~/.ssh/id_ed25519_flow_demo ubuntu@163.227.121.206`.
