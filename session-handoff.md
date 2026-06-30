# Session Handoff — review sáng 2026-07-01

## Current Objective
- **Goal:** feat-007 — sửa độ chính xác đếm YOLO (sót ~75% so Axis do backlog frame).
- **Status:** **CODE XONG trên nhánh riêng + PR mở. CHƯA merge, CHƯA deploy.** Chờ bạn review.
- **Branch:** `feat/yolo-counting-thread-fix` (off `main` @ harness commit). PR: xem link tôi gửi qua notification.

## Làm gì qua đêm (autonomous, theo flow dcnet-workflow)
1. ✅ Chạy thử **tối ưu description** skill dcnet-workflow → kết quả: **giữ nguyên** (benchmark trong env không đo được trigger — recall 0% mọi candidate = artifact; KHÔNG đổi SKILL.md). Trung thực: trial cho thấy harness đo trigger không chạy ở đây.
2. ✅ feat-007: viết **spec** [docs/specs/2026-06-30-yolo-counting-accuracy-fix-design.md](docs/specs/2026-06-30-yolo-counting-accuracy-fix-design.md) + **plan** [docs/plans/2026-06-30-yolo-counting-accuracy-fix.md](docs/plans/2026-06-30-yolo-counting-accuracy-fix.md).
3. ✅ **Implement** `_LatestFrameGrabber` (thread giữ frame mới nhất) + refactor `_counting_loop` trong [fall_detection_web/monitor.py](fall_detection_web/monitor.py). Chỉ sửa 1 file. Giữ nguyên logic crossing/snapshot/lifecycle.
4. ✅ **Verify cơ chế** (read-only trên prod, KHÔNG đổi prod): probe → threaded reader **backlog=0**, luôn realtime (skip stale không queue, lag ~0.4 frame) vs sequential cũ 8.5s tăng dần. `init.sh` OK.

## Verification Evidence
| Check | Kết quả |
|---|---|
| `python3 -m py_compile monitor.py` + `./init.sh` | OK |
| Probe threaded reader (prod container, read-only) | grabber 352f/20s, processed 276, **backlog 0**, lag ~0.4 frame |
| Accuracy thật (Axis vs YOLO) | ⏳ **CHƯA đo** — cần deploy + chạy live ≥1h (gate đóng feat-007) |

## ⚠️ CẦN BẠN QUYẾT (feat-008) trước khi merge/deploy
**Có triển khai camera KHÔNG-native-detect (buộc dùng YOLO đếm) không?**
- **Có** → merge PR → deploy: rebuild image + đổi `cameras.rtsp_url`→substream cam thật + chạy đối chứng ≥1h. Tôi làm tiếp khi bạn OK.
- **Không (toàn cam Axis)** → có thể KHÔNG merge; chỉ tắt `yolo_counting` trên cam Axis, dùng Axis (chính xác ~99%, ~0 CPU). feat-007 đóng "won't-deploy". PR vẫn hữu ích cho công cụ dual-counting test.

## Files Changed (trên nhánh, chưa merge)
- `fall_detection_web/monitor.py` (threaded reader)
- `docs/specs/2026-06-30-yolo-counting-accuracy-fix-design.md`, `docs/plans/2026-06-30-yolo-counting-accuracy-fix.md` (mới)
- `feature_list.json` (feat-007 evidence), `progress.md`, `session-handoff.md`

## Next Session Startup
1. Đọc section Harness đầu `CLAUDE.md` → `feature_list.json` + `progress.md` + handoff này.
2. Xem PR `feat/yolo-counting-thread-fix` (diff monitor.py + spec/plan).
3. Chốt feat-008 → tôi merge+deploy hoặc đóng won't-deploy.
4. KHÔNG có gì đang chạy trên prod khác thường; prod vẫn live bình thường (chưa đụng).

## Recommended Next Step
Review PR + trả lời feat-008. Nếu "Có": tôi rebuild image + đổi rtsp_url substream + deploy + đo Axis-vs-YOLO ≥1h để đóng feat-007.
