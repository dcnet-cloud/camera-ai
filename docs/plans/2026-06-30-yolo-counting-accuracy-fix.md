# YOLO Counting Accuracy Fix — Implementation Plan (feat-007)

> **For agentic workers:** thực hiện theo `dcnet-workflow` (superpowers subagent-driven/executing-plans). Spec: [docs/specs/2026-06-30-yolo-counting-accuracy-fix-design.md](../specs/2026-06-30-yolo-counting-accuracy-fix-design.md).

**Goal:** Engine đếm YOLO luôn xử lý frame mới nhất (hết backlog) → track ID liền mạch → bắt đủ lượt.

**Architecture:** Thêm thread `_LatestFrameGrabber` giữ 1-slot frame mới nhất; `_counting_loop` chỉ chạy `model.track` khi có frame mới (seq đổi). Giữ nguyên mọi logic crossing/snapshot/lifecycle.

**Tech Stack:** Python, OpenCV (cv2), ultralytics YOLO, threading.

## Global Constraints
- Code-only trong `fall_detection_web/monitor.py`. KHÔNG deploy, KHÔNG đổi prod DB, KHÔNG đổi logic crossing.
- Giữ tương thích: events `counter_yolo`, snapshot path, `start/stop/restart_counting`, `counting_stop_event`.

## Tasks

### Task 1 — Thêm class `_LatestFrameGrabber`
- [ ] Viết class trước `_counting_loop`: `__init__(rtsp_url, stop_event)`, `start()`, `latest() -> (frame|None, seq)`, thread `_run()` đọc liên tục, ghi đè latest dưới lock, tự reconnect backoff `min(2**fail,30)s` (dùng `stop_event.wait`), set `CAP_PROP_BUFFERSIZE=1`, `finally` release.
- [ ] Test: `python3 -m compileall` pass.

### Task 2 — Refactor capture trong `_counting_loop`
- [ ] Thay khối `cap = VideoCapture` + `cap.read()` + reconnect bằng: tạo `_LatestFrameGrabber`, `.start()`; vòng lặp lấy `latest()`, bỏ qua nếu `seq` chưa đổi (`stop_event.wait(0.01)`), xử lý khi frame mới.
- [ ] Giữ NGUYÊN: ROI, x-range, resolve_side/crossing_direction, snapshot imwrite, insert_counting_event, dọn track_sides, log start/stop.
- [ ] `finally`: stop grabber (release).
- [ ] Test: compileall + `./init.sh` pass.

### Task 3 — Verify (read-only, không đổi prod)
- [ ] Probe trong container prod: threaded reader + track 20s → đo staleness ~0 (so backlog 8.5s cũ). Lưu output vào PR.
- [ ] Không deploy. Không đổi rtsp_url.

### Task 4 — Harness state + ship
- [ ] feature_list.json: feat-007 evidence = "code-complete trên nhánh; threaded reader; accuracy chờ đo live ở deploy". Giữ `in-progress` (chưa done tới khi đo live).
- [ ] progress.md + session-handoff.md cập nhật.
- [ ] Commit + push nhánh; `gh pr create` (KHÔNG merge). PR body nêu rõ feat-008 decision + verification status.

## Deploy-time (NGOÀI plan này, sau khi user duyệt)
1. Rebuild image fall_detection_web.
2. Đổi `cameras.rtsp_url` → substream cam thật (độ phân giải thấp).
3. Chạy đối chứng Axis vs YOLO ≥1h giờ cao điểm → đo MAE IN/OUT → đóng feat-007 nếu khớp 70–90%.
