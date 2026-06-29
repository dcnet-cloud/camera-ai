# Design — Test song song 2 bộ đếm người ra/vào (Camera-event vs YOLO)

- **Ngày:** 2026-06-29
- **App:** `fall_detection_web`
- **Mục tiêu:** Chạy đồng thời 2 cách đếm người ra/vào (KHÔNG định danh) trên cùng 1 camera để **so sánh độ chính xác thực tế**:
  1. **Camera-event** — sự kiện line-crossing do camera Axis bắn ra (đã có pipeline).
  2. **YOLO** — đếm bằng YOLO chạy trên máy local (xây mới).
- **Phạm vi dữ liệu:** chỉ **hôm nay** (VN+7), **theo từng camera**.
- **UI:** Trang CHI TIẾT camera, thêm 2 block ngay dưới card "go2rtc Source".

---

## 1. Bối cảnh (đã audit codebase)

- **Block camera-event gần như KHÔNG cần code mới.** Camera Axis bật Object Analytics line-crossing → publish MQTT (`.../ObjectAnalytics/Device1Scenario1` = IN, `Scenario2` = OUT) → `services/event_collector` parse (`parser.py`) → ghi bảng `events` (`type='counter'`, `direction='in'/'out'`, `cam_id`, `ts`). Hàm `db.counting_occupancy_today(cam_id)` đã đếm theo cam + hôm nay VN+7, occupancy = `max(0, in-out)`.
- **Block YOLO chưa có gì.** `monitor.py:_monitor_loop` chỉ `model.predict(classes=[0])` từng frame độc lập (đếm số người để trigger verify té ngã). **Không tracking, không line-crossing, không in/out.**
- **Capture đã tách:** `frame_holders[index]` + capture threads cập nhật frame mới nhất cho mỗi cam — luồng đếm YOLO sẽ đọc ké, không decode lại RTSP.
- **events ↔ cameras:** `events.cam_id REFERENCES cameras(id)`; `db.cam_id_for(cam_uid)` resolve.
- **Chưa có** endpoint/UI reset hay set occupancy thủ công.

### Camera test thật (cam_door, Axis)
- RTSP Axis `axis-media/media.amp`. Góc rộng (fisheye), gắn cao nhìn chéo xuống phòng.
- **Cửa kính trong suốt ở giữa-trên khung** → YOLO nhìn xuyên thấy hành lang ngoài. Người ngoài hành lang nằm **phía trên vạch** → logic băng-qua không đếm trừ khi họ thật sự đi qua cửa.
- Trên ảnh có sẵn 2 ô mũi tên Axis (↓ "1136", ↑ "1058") nằm đúng khu cửa giữa — đây là bộ đếm Axis = nguồn của block camera-event. Đặt vạch YOLO trùng vùng này để so sánh công bằng.

---

## 2. UI — 2 block dưới "go2rtc Source"

Thêm vào `templates/camera_detail.html` (sau card go2rtc Source, `~:728`), trong `aside#cameraMetrics`. Dữ liệu load qua JS, poll mỗi **~3s** (như trang counting).

- **Block A — 📷 Camera (Axis):** viền/nhãn **xanh dương** (`--bg-accent`/`--text-accent`). 3 số lớn: **VÀO · RA · ĐANG TRONG PHÒNG**. Nhãn "hôm nay".
- **Block B — 🤖 YOLO (máy local):** viền/nhãn **cam** (`--bg-warning`/`--text-warning`). 3 số lớn tương tự.
- **Form cấu hình vạch YOLO:** nằm trong block B, **thu gọn mặc định**, click (icon ⚙/▸) mới bung ra để setup. Trường: `Bật đếm YOLO` (toggle), `Vạch ngang Y (%)`, `Đoạn X bắt đầu (%)`, `Đoạn X kết thúc (%)`, `Dịch chuyển tối thiểu (%)`, `Đảo chiều (xuống = RA)`, nút **Lưu vạch**.
- **Nút Reset (dùng chung 2 block):** input số người đang trong phòng + nút "Đặt lại hôm nay".

Mặc định vạch (đã review trên frame thật): `line_y=51`, `x_start=44`, `x_end=71`, `min_disp=6`, `invert=false`.

---

## 3. Dữ liệu & lưu trữ

### 3.1 YOLO crossings → bảng `events` (tái dùng)
Ghi vào chính bảng `events` với `type='counter_yolo'`, `direction='in'/'out'`, `cam_id`, `ts=now()`, `payload` (track_id, conf, vị trí). Lợi:
- Tái dùng bucketing hôm-nay-VN + truy vấn theo cam sẵn có.
- **Regression-safe:** trang/counting cũ lọc `type='counter'` nên KHÔNG dính `counter_yolo`.
- Persist qua restart.

Helper mới: `db.insert_counting_event(cam_id, direction, ts, source)` với `source ∈ {'counter','counter_yolo'}` (map ra cột `type`). Idempotent không bắt buộc (YOLO không retry như MQTT) nhưng dùng INSERT thường.

### 3.2 Mốc reset → bảng mới `counting_baseline`
```sql
CREATE TABLE IF NOT EXISTS counting_baseline (
    cam_id     INT PRIMARY KEY REFERENCES cameras(id),
    reset_ts   TIMESTAMPTZ NOT NULL,
    baseline   INT NOT NULL CHECK (baseline >= 0)
)
```
Reset → `INSERT ... ON CONFLICT (cam_id) DO UPDATE` set `(reset_ts=now(), baseline=N)`. **1 row/cam, dùng chung cho cả 2 block.**

### 3.3 Công thức mỗi block (nguồn `src` = `counter` hoặc `counter_yolo`)
Cho `cam_id` + ngày VN hôm nay:
- Đọc baseline row. Nếu tồn tại VÀ `reset_ts` thuộc hôm nay (VN): `N = baseline`, cửa sổ đếm = `(reset_ts, now]`. Ngược lại: `N = 0`, cửa sổ = `[đầu ngày VN, now]`.
- `VÀO = N + COUNT(events: cam_id, type=src, direction='in', ts trong cửa sổ)`
- `RA  = 0 + COUNT(events: cam_id, type=src, direction='out', ts trong cửa sổ)`
- `ĐANG TRONG PHÒNG = max(0, VÀO − RA)`

Helper mới: `db.counting_block(cam_id, src) -> {"in","out","occupancy"}` (dùng cho cả 2 block, đọc baseline 1 lần ở route rồi truyền vào để tránh query thừa).

### 3.4 Cấu hình vạch → JSONB trên `cameras`
Thêm cột `yolo_counting JSONB` (default `'{}'::jsonb`) trên bảng `cameras` (tạo trong `init_db`, thêm `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` cho DB cũ). Shape:
```json
{"enabled": true, "line_y": 51, "x_start": 44, "x_end": 71, "min_disp": 6, "invert": false}
```
Helper: `db.get_yolo_counting(cam_id)` / `db.set_yolo_counting(cam_id, cfg)` (validate range 0..100, x_start < x_end).

---

## 4. Engine đếm YOLO — luồng riêng, dùng lại frame capture

Trong `_monitor_loop`, sau khi capture threads start: với mỗi cam có `yolo_counting.enabled=true`, spawn **1 thread đếm riêng** `_counting_loop(index, camera, frame_holder, lock, cfg)`.

- Load **model YOLO thứ 2** (tách khỏi model predict của fall-detect, tránh xung đột tracker state). Dùng cùng `config["yolo_model"]`.
- Vòng lặp: lấy frame mới nhất từ `frame_holder` (theo `seq`), chạy `model.track(frame, persist=True, classes=[0], verbose=False)` — **không frame_skip** (tracking cần liên tục).
- Với mỗi track có id + bbox, tính **tâm** `(cx, cy)`. Chỉ xét track có `cx` nằm trong đoạn `[x_start, x_end]` (theo % chiều rộng).
- **Line-crossing + dead-band (dịch chuyển tối thiểu):**
  - Vạch tại `y_line = line_y% * H`. Dead-band = `±(min_disp% * H)`.
  - Theo dõi "side" mỗi track: `above` nếu `cy < y_line - band`, `below` nếu `cy > y_line + band`, else giữ side cũ (vùng đệm = không đổi side).
  - Khi side đổi `above→below`: +1 **VÀO** (hoặc RA nếu `invert`). `below→above`: +1 **RA** (hoặc VÀO nếu invert). Mỗi lần đổi side đếm 1 lần (debounce tự nhiên do phải đi hết dead-band mới đổi).
  - Insert `db.insert_counting_event(cam_id, direction, now, 'counter_yolo')`.
- Track mất (ultralytics hết id) → quên state. Giữ `dict track_id -> last_side` có dọn rác theo thời gian.
- Tôn trọng `stop_event`; thread daemon; log throttled như YOLO hiện tại.

**Giới hạn (ghi rõ):** tracker chuẩn cho **1 cam test**. Đa-cam đồng thời cần model.track persist riêng từng cam (id collision nếu share) — ngoài phạm vi lần này; spec sau mở rộng.

**Hot-reload:** đổi cấu hình vạch hoặc bật/tắt → `restart_monitor` (như các thay đổi config khác) để luồng đếm áp cấu hình mới. (Đơn giản, chấp nhận gián đoạn ngắn.)

---

## 5. API (trong `app.py`)

- `GET /api/camera/{name}/counting`
  → `{"date": "<vn-today>", "camera": {in,out,occupancy}, "yolo": {in,out,occupancy}, "reset_ts": <iso|null>}`
  (resolve cam_id theo name → đọc baseline → `counting_block(cam_id,'counter')` + `counting_block(cam_id,'counter_yolo')`).
- `POST /api/camera/{name}/counting/reset` body `{"occupancy": N}` (N≥0)
  → upsert `counting_baseline`, trả số mới của cả 2 block.
- `POST /api/camera/{name}/yolo-counting-config` body = shape §3.4
  → validate + `set_yolo_counting` → `restart_monitor` → trả cfg đã lưu.

Tất cả gắn `Depends(auth.require_auth)` như route khác.

---

## 6. Phụ thuộc ops (không phải code) — "nhận event từ cam"

Block camera-event chỉ có số khi:
1. `event_collector` đang chạy + nối broker (TLS, client-id riêng `event_collector_cameraai`).
2. Camera Axis đã seed trong bảng `cameras` với `cam_uid` khớp serial trong topic.
3. Camera bật Object Analytics line-crossing (Scenario1/2).

Nếu collector chưa chạy → block hiện 0 (không lỗi). Đây là việc cấu hình/chạy service.

---

## 7. Cắt bớt (YAGNI)

- Không thêm biểu đồ giờ cho 2 block (trang `/counting` đã có hourly).
- Không UI vẽ line bằng canvas (chọn vạch ngang + đoạn X bằng số).
- Không xử lý đa-cam tracker (1 cam test).
- Không retry/idempotent đặc biệt cho YOLO events.

---

## 8. Liên quan migration platform

Tính năng này **độc lập** với 5 phase migration (là công cụ test/đối chiếu). Reuse hạ tầng Phase 0 (Postgres `events`/`cameras`) + Phase 1 (counting) + Phase 3 (per-camera flags). `type='counter_yolo'` là giá trị mới, không đụng query Phase 1.

---

## 9. Tiêu chí hoàn thành

- [ ] Trang chi tiết cam hiện 2 block phân biệt rõ (xanh/cam), 3 số mỗi block, chỉ hôm nay VN+7, poll ~3s.
- [ ] Form cấu hình vạch thu gọn, bung ra setup được, lưu vào `cameras.yolo_counting`.
- [ ] Bật cấu hình → luồng đếm YOLO chạy `model.track`, đếm in/out qua vạch + đoạn X + dead-band, ghi `events(type='counter_yolo')`.
- [ ] Nút reset set baseline → cả 2 block về `VÀO=N, RA=0, occupancy=N`, đếm tiếp từ thời điểm reset.
- [ ] Trang `/counting` cũ KHÔNG đổi số (không dính `counter_yolo`).
- [ ] Verify thủ công bằng cách đi qua cửa thật, đối chiếu số YOLO vs số Axis.
</content>
</invoke>
