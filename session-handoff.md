# Session Handoff

## Current Objective

- **Goal:** Sửa độ chính xác đếm YOLO (`feat-007`) — YOLO sót ~75% so Axis, tệ nhất hướng IN.
- **Current status:** Root-cause đã xác nhận bằng đo trực tiếp; **chưa implement fix**. Chờ user quyết `feat-008`.
- **Branch / commit:** `main` @ `593f9d6` (đã deploy prod PR #11 + backport compose).

## Completed This Session

- [x] Deploy PR #11 (RBAC + retry snapshot) lên prod, verify pass.
- [x] Backport event_collector snapshot vol/env vào `docker-compose.prod.yml` (main↔prod khớp) + ghi CLAUDE.md.
- [x] Debug sâu lệch Axis vs YOLO (17–18h) → tìm + đo xác nhận root cause.
- [x] Dựng Harness Engineering (file này + feature_list.json + progress.md + init.sh + section CLAUDE.md).

## Verification Evidence

| Check | Command | Result | Notes |
|---|---|---|---|
| Prod auth gate | `curl -s -o /dev/null -w "%{http_code}" https://camera-test.dcnet.vn/api/counting` | 401 | ✓ sau deploy PR #11 |
| Đếm vẫn chảy | `SELECT type,count(*) FROM events WHERE ts::date=today GROUP BY type` | counter 88 / counter_yolo 21 | ✓ |
| YOLO frame delivery | probe in-container | 16.9 FPS, 0 fail | nguồn KHÔNG nghẽn |
| YOLO loop throughput | probe emulate loop | 9.8 FPS → backlog 8.5s | ⭐ root cause |

## Files Changed (phiên này)

- `docker-compose.prod.yml`, `CLAUDE.md` (commit 593f9d6, đã push)
- MỚI (harness, chưa commit): `feature_list.json`, `progress.md`, `init.sh`, `session-handoff.md`, section Harness trong `CLAUDE.md`

## Decisions Made

- Cam Axis → dùng Axis, tắt YOLO; chỉ cam non-detect mới cần YOLO (xem progress.md).
- Lệch không do "bật cả 2 counter" mà do bug throughput engine YOLO.

## Blockers / Risks

- **feat-008 (blocker feat-007):** chờ user quyết có triển khai cam non-detect (cần YOLO thật) không. Nếu KHÔNG → có thể chỉ tắt yolo_counting trên cam Axis, không cần fix engine.

## Next Session Startup

1. Đọc section "Harness" đầu `CLAUDE.md`.
2. Đọc `feature_list.json` + `progress.md`.
3. Đọc handoff này.
4. Chạy `./init.sh` trước khi sửa.

## Recommended Next Step

- Hỏi/chốt `feat-008`. Nếu có cam non-detect → implement thread latest-frame trong `_counting_loop` ([fall_detection_web/monitor.py:1332]) + đổi `cameras.rtsp_url` sang substream cam thật → deploy → đối chứng Axis vs YOLO ≥1h.
