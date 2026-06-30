# YOLO Counting Accuracy Fix — Design (feat-007)

**Ngày:** 2026-06-30 · **Trạng thái:** Design + impl trên nhánh `feat/yolo-counting-thread-fix`, CHỜ DUYỆT (review async qua PR). KHÔNG deploy prod khi chưa duyệt.

> Spec này viết autonomous (user đi ngủ, dặn "triển khai luôn, review sáng mai"). Gate duyệt của brainstorming/superpowers được thoả bằng **review PR** thay vì duyệt-trực-tiếp.

## 1. Vấn đề (đã đo, không phải giả thuyết)

Bộ đếm YOLO (`type='counter_yolo'`) sót **~75%** so với Axis (`type='counter'`) — ngày 2026-06-30: Axis 88 / YOLO 21. Tệ nhất hướng **IN** (live đo 0/4 IN). Sót theo **cụm** (cả khối 30' = 0), không đều.

**Root cause (đo trực tiếp trên prod):**
- go2rtc restream giao **16.9 FPS, 0 lỗi** → nguồn KHÔNG phải nút thắt.
- `_counting_loop` xử lý **~9.8 FPS < 17 FPS nguồn** → `CAP_PROP_BUFFERSIZE=1` **bị FFMPEG/RTSP bỏ qua** → loop đọc **backlog frame cũ, trễ tăng vô hạn (đo: +143 frame/20s = 8.5s và tăng)**.
- Hệ quả: ByteTrack (`persist=True`) giả định frame liên tục thời-gian-thực; khi cadence giật + reconnect xả buffer → **đổi track ID giữa lượt** → máy trạng thái line-crossing mất `prev_side` → **không tính lượt** (lượt nhanh/IN mất nhiều nhất).

Chi tiết đo: [docs/2026-06-30-dual-counting-yolo-tuning.md] + lịch sử session.

## 2. Mục tiêu

Cho engine đếm YOLO **luôn xử lý frame mới nhất theo thời gian thực** (độ trễ bị chặn), để track ID liền mạch qua vạch → bắt đủ lượt. Mục tiêu định lượng (đo ở deploy): kéo độ khớp với Axis từ ~24% lên ~70–90% (phần dư = người xa nhỏ/occlusion/substream — xem §5).

## 3. Thiết kế

### 3.1 Thread đọc giữ frame mới nhất (CỐT LÕI — fix này)
Thêm `_LatestFrameGrabber` (thread riêng):
- Mở `cv2.VideoCapture(rtsp_url)`, vòng lặp `cap.read()` ở tốc độ nguồn, **ghi đè** giữ duy nhất frame mới nhất + `seq` (đếm tăng) dưới `threading.Lock`.
- Tự reconnect (backoff `min(2**fail, 30)s`, dùng `stop_event.wait` để dừng nhạy); khi đang reconnect → latest=None.
- `latest()` trả `(frame, seq)` hoặc `(None, seq)`.

`_counting_loop` đổi: KHÔNG `cap.read()` trực tiếp nữa. Mỗi vòng lấy `latest()`; **chỉ chạy `model.track` khi `seq` đổi** (frame mới) — nếu chưa có frame mới thì `stop_event.wait(0.01)` rồi tiếp. → loop tự pace theo nguồn, **không backlog**, luôn xử lý frame "bây giờ", spacing đều → ByteTrack khớp ID ổn định.

**Giữ NGUYÊN** (không đụng): logic ROI, x-range, `resolve_side`/`crossing_direction`, snapshot, `insert_counting_event`, dọn `track_sides`, vòng đời `counting_stop_event`/`start/stop/restart_counting`.

### 3.2 Substream / cam thật (DEPLOY-TIME, không phải code)
`cameras.rtsp_url` prod đang trỏ `rtsp://go2rtc:8554/cam_door` (restream 4MP). Nên trỏ về **substream độ phân giải thấp của cam thật** (giảm decode + người gần imgsz hơn). Đây là **đổi config/DB lúc deploy**, KHÔNG nằm trong code PR này (tránh đụng prod DB qua đêm). Ghi rõ ở plan như bước deploy.

## 4. Phạm vi

- **TRONG PR:** chỉ sửa `fall_detection_web/monitor.py` (`_LatestFrameGrabber` + refactor capture trong `_counting_loop`). Code-only.
- **NGOÀI PR (deploy-time):** rebuild image, đổi `rtsp_url` substream, chạy đối chứng.
- **KHÔNG:** deploy prod, đổi prod DB, đổi logic crossing.

## 5. ⚠️ Điểm quyết định feat-008 (cần user chốt khi review)

Fix này chỉ đáng MERGE/DEPLOY nếu **có triển khai camera KHÔNG-native-detect** (buộc dùng YOLO đếm).
- Cam Axis (như "DCNET - Lầu 2") nên dùng **Axis** (chính xác ~99%, ~0 CPU) → chỉ cần TẮT `yolo_counting`, **không cần fix này**.
- Nếu toàn bộ là cam Axis → PR này có thể **để đó** (cải thiện công cụ dual-counting test) hoặc đóng "won't-deploy".

→ Review sáng mai: chốt feat-008 trước khi merge.

## 6. Verification

- **Overnight (làm được):** `./init.sh` (syntax/compile); probe read-only trong container prod chứng minh threaded reader → staleness ~0 (so 8.5s cũ). KHÔNG đổi prod.
- **Acceptance thật (ở deploy, sau khi user duyệt):** chạy đối chứng Axis vs YOLO ≥1h giờ cao điểm; đo MAE IN/OUT; kỳ vọng khớp 70–90%. Đây là gate đóng feat-007.

## 7. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Thread reader rò RTSP khi stop | `finally` release; join timeout; tôn trọng `counting_stop_event` |
| Vẫn sót do người xa nhỏ (4MP→imgsz640) | substream + imgsz (deploy-time §3.2), ngoài phạm vi code này |
| Đổi engine ảnh hưởng trang dual-counting | giữ nguyên I/O (events `counter_yolo`, snapshot) → trang không đổi |
| Chưa verify accuracy thật khi merge | gate acceptance §6 ở deploy; PR nói rõ "code-complete, accuracy chờ đo live" |
