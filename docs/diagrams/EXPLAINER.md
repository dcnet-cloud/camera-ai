# Luồng hoạt động hệ thống — DCNET Camera AI

Bộ sơ đồ này phân tích chi tiết luồng hoạt động của **`fall_detection_web`** — ứng dụng giám sát camera AI (YOLO phát hiện người → AI Vision xác minh → cảnh báo Telegram → ghi hình + timeline). Mở [`index.html`](index.html) để xem gallery; mỗi sơ đồ là một file HTML độc lập, có nút chuyển sáng/tối và xuất PNG/SVG.

> Hai sơ đồ cuối (`ai-verification-sequence`, `ai-data-pipeline`) là **phần chuyên sâu dành riêng cho luồng hoạt động của AI** theo yêu cầu.

## 1. Kiến trúc hệ thống — [`system-architecture.html`](system-architecture.html)

Toàn cảnh thành phần và 3 vùng triển khai:

- **Camera LAN**: IP Camera (RTSP H.264/H.265).
- **VPS · fall_detection_web**: `go2rtc` (frame.jpeg), **Capture Thread** (mỗi camera 1 luồng, `buffer=1`, ghi frame + `seq`), **Monitor Loop** (`_monitor_loop`, dùng `threading.Lock`), **YOLOv8** (phát hiện person — `classes=[0]`), **AI Client** (`verify_scene()`), **SQLite WAL** (events/recordings), **Redis** (cache fail-open, tuỳ chọn), **Web UI** (Dashboard/Live, auth JWT cookie).
- **Cloud**: **AI Vision API** (OpenAI-compatible, primary + fallback), **Telegram** (sendPhoto), **Teldrive** (Telegram VFS, Static API Key).

Ưu tiên nguồn ảnh: `go2rtc frame.jpeg` → RTSP IP camera (fallback).

## 2. Luồng vận hành Monitor — [`operational-workflow.html`](operational-workflow.html)

Sơ đồ swimlane theo 5 làn: **Camera & Stream → Capture & YOLO → AI Verification → Cảnh báo & Ghi hình → Lưu trữ & Timeline**. Các quyết định mấu chốt:

- YOLO chỉ chạy 1/N khung hình (`frame_skip`); vòng lặp nghỉ `loop_sleep`.
- Chỉ gọi `verify_scene` khi **có person** *và* đã quá `verify_interval`.
- Verdict **EMERGENCY** mới kích hoạt cảnh báo; Telegram bị chặn nếu chưa quá `alert_cooldown`.
- Ghi clip chạy thread riêng (`record_and_upload_clip`, copy-codec để nhẹ CPU), giới hạn bởi `record_cooldown`.
- Mọi giai đoạn đều `log_event` vào SQLite.

## 3. 🤖 Luồng AI · `verify_scene` — [`ai-verification-sequence.html`](ai-verification-sequence.html)

Sơ đồ trình tự (sequence) mô tả tương tác theo thời gian giữa `Monitor Loop`, `verify_scene`, model chính/fallback, parser, Telegram và SQLite:

1. **Gọi model chính** — `POST /chat/completions` kèm `text` + `image_url` (base64 data URL), `max_tokens=1000`, timeout 120s.
2. **Fallback khi lỗi** — `RuntimeError` (HTTP/timeout) ở model chính → tự thử `fallback_vision_model` (nếu có cấu hình và khác model chính). 3 lần lỗi liên tiếp → tạm ngưng gọi AI (backoff) và cảnh báo Telegram.
3. **Parse verdict** — `response_ai_content()` đọc được SSE / JSON nối / JSON thường; `strip_thinking_content()` bỏ `<think>…</think>`; tách `(result, description, raw)`.
4. **Cảnh báo & log** — EMERGENCY + quá cooldown → `sendPhoto`; ghi `log_event`.

## 4. 🤖 Luồng AI · dữ liệu — [`ai-data-pipeline.html`](ai-data-pipeline.html)

Sơ đồ dataflow theo đường đi của **dữ liệu ảnh** qua 5 chặng: **Khung hình → Chuẩn bị → Vision API → Parse → Đầu ra**:

- Snapshot (`latest.jpg`) → base64 data URL (`image_to_data_url`); prompt chọn theo `prompt_id` của camera, mặc định `verify_prompt`.
- `text` + `image_url` → `vision_model`; lỗi → nhánh `fallback_vision_model`; cả hai trả về cùng dạng `content`.
- `content` → `strip thinking` → `parse verdict` → `(result, desc, raw)`.
- Phân nhánh đầu ra: `SAFE/EMERGENCY`; chỉ EMERGENCY mới `Telegram sendPhoto`; mọi case đều `log_event` (events/recordings).

---

**Chỉnh sửa:** sửa JSON-IR trong [`src/`](src/) rồi chạy lại renderer tương ứng (`node renderers/<type>/render-<type>.mjs src/<name>.json <name>.html`) và `python3 scripts/build_gallery.py manifest.json .` để dựng lại gallery.
