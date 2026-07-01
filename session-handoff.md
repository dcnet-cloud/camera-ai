# Session Handoff — ⏸️ POC TẠM NGỪNG (2026-07-01)

## Trạng thái chốt
- **POC camera-ai tạm ngừng** ở trạng thái **prod LIVE, ổn định**. Không có việc dở dang bắt buộc.
- Prod: `https://camera-test.dcnet.vn` (VM `ssh camera`) — đếm Axis + YOLO + thumbnail + feed sạch + timeline realtime. main = `7f22b9a`, nhánh sạch.
- feat-001..007 done; feat-008 deferred. Chi tiết `feature_list.json` + `progress.md`.

## Session cuối (2026-07-01) làm gì
1. ✅ **Fix Axis-thumb + YOLO-data sau cutover** (PR #12): seed `go2rtc_src=cam_door` + `rtsp_url` reachable; collector mount `fdw_data` + `COUNTING_SNAPS_DIR`. → cả 2 counter có thumbnail.
2. ✅ **Vạch YOLO khớp choke cửa**: `line_y=52, x[47,68]` (user vẽ vạch vàng xác nhận lối ra/vào). Config prod `cameras.yolo_counting`.
3. ✅ **Feed sạch**: tắt overlay burn-in cam Axis — VAPIX `Image.I0.Appearance.Overlays=off` (cần restart go2rtc mới ăn). KHÔNG ảnh hưởng đếm MQTT. → YOLO ăn frame gốc.
4. ✅ **feat-007 fix trễ YOLO** (PR #14, supersede + đóng PR #13): reader thread grab-latest (drop backlog) + capture-timestamp (timeline đúng) + stream đếm 720p@15 `cam_count` (reader theo kịp). Verify: reader 28fps≥15, RAM 2.79GiB→733MiB, engine ổn định.
5. ✅ Update state + CLAUDE.md; đóng PR #13 (+ xoá nhánh `feat/yolo-counting-thread-fix`).

## Việc CHƯA đóng (khi resume)
| # | Việc | Ghi chú |
|---|------|---------|
| 1 | **Đo accuracy live Axis-vs-YOLO ≥1h** | feat-007 gate còn hở. Engine đã fix realtime nhưng chưa đo MAE IN/OUT. Chạy 1 config cố định, không restart, ≥1h. |
| 2 | **feat-008** — quyết cam non-detect | Có triển khai cam không-native-detect (cần YOLO thật) không → quyết giá trị engine YOLO. |
| 3 | **Miss người VÀO** ở cửa (YOLO) | Người nhỏ/xa lúc băng vạch → sót IN. Nếu cần: imgsz 960/yolov8s (đánh đổi CPU). |
| 4 | **Ops user tự xử** | O6 cron backup postgres; `go2rtc_url`→`/live` live-view; tắt instance lạ `171.243.48.224`. |
| 5 | **AI Vision prod** | Dev chạy (Gemini/9router); prod chưa cấu hình + chưa nhập Telegram token. |

## Rủi ro mang theo
- **KHÔNG rollback về DCNET** (DB cũ đã xoá). Deploy phải verify kỹ.
- **Hạ tầng chung GIỮ**: `mosquitto` (cam Axis ingest — camera-ai đọc ké) + `caddy` KHÔNG được tắt.
- Prod DB mới tinh — config YOLO/AI không sang từ dev.

## Resume Startup
1. Đọc section Harness đầu `CLAUDE.md` → `feature_list.json` (`_status`) + `progress.md` (mục "Khi resume") + handoff này.
2. `./init.sh` verify baseline. Prod access `ssh camera`.
3. Nếu resume feat-007 gate: deploy đã sẵn, chỉ cần chạy đo accuracy ≥1h. Nếu resume feat-008: brainstorm scope cam non-detect.
4. Prod đang chạy bình thường — KHÔNG cần can thiệp gì để giữ live.

## Rollback nhanh (nếu cần)
- Overlay cam: VAPIX `Image.I0.Appearance.Overlays=all` (`--anyauth`, root, `https://<CAM_IP>:8443`).
- Vạch/engine YOLO: sửa `cameras.yolo_counting` qua UI `/camera/{name}` hoặc tắt `enabled`.
