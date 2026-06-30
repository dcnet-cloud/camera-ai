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

## What's In Progress

- [ ] feat-007 — **fix engine đếm YOLO**
  - Root cause (đo trực tiếp 2026-06-30): `_counting_loop` ([fall_detection_web/monitor.py:1332]) xử lý **9.8 FPS < nguồn 17 FPS** → `CAP_PROP_BUFFERSIZE=1` bị FFMPEG/RTSP bỏ qua → **backlog frame cũ tăng vô hạn (8.5s)** → ByteTrack đứt track ID giữa lượt → sót lượt (tệ nhất IN: live đo 0/4 IN).
  - Bằng chứng phụ: go2rtc restream giao 16.9 FPS/0 lỗi (nguồn KHÔNG phải nút thắt); inference 12 FPS standalone; cam "DCNET - Lầu 2" bật cả fall_detection (YOLO #2) + counting (YOLO) trên cùng CPU.
  - Fix dự kiến: (1) thread đọc riêng giữ frame mới nhất; (2) đổi `cameras.rtsp_url` từ `rtsp://go2rtc:8554/cam_door` → cam thật + substream độ phân giải thấp.

## What's Next

1. **User quyết feat-008** (có cam non-detect cần YOLO không) → định mức đầu tư feat-007.
2. Implement thread latest-frame trong `_counting_loop` (+ tùy chọn substream).
3. Deploy → chạy đối chứng Axis vs YOLO ≥1h giờ cao điểm → đo MAE IN/OUT (verify feat-007).

## Blockers / Risks

- [ ] **feat-008 blocker**: chưa rõ có triển khai cam non-detect → có thể không cần fix engine cho cam Axis (chỉ tắt YOLO, dùng Axis).
- [ ] **Rollback prod**: KHÔNG rollback về DCNET được (DB cũ đã xoá). Mọi deploy phải verify kỹ.
- [ ] **Prod DB mới tinh**: config YOLO dual-counting không sang từ dev; cấu hình lại trên prod nếu cần.

## Decisions Made

- **2026-06-30**: Chiến lược production — cam có native detect dùng Axis (chính xác, ~0 CPU); chỉ cam non-detect dùng YOLO. Dual-counting là công cụ test, không phải config production.
- **2026-06-30**: Nguồn lệch Axis/YOLO KHÔNG do "bật cả 2 counter" (Axis độc lập ~0 CPU) mà do bug throughput engine YOLO + YOLO #2 (fall_detection) giành CPU.

## Notes for Next Session

Đọc `docs/2026-06-30-dual-counting-yolo-tuning.md` để biết lịch sử tuning YOLO. Root-cause feat-007 đã đo xong — vào thẳng implement khi user chốt feat-008. Prod access: `ssh -i ~/.ssh/id_ed25519_flow_demo ubuntu@163.227.121.206`.
