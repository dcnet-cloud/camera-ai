#!/usr/bin/env bash
# Harness init — cổng verify NHANH cho monorepo camera-ai (KHÔNG cài torch/deps nặng).
# Mục tiêu: bắt lỗi cú pháp Python + lỗi cấu hình docker-compose trước khi sửa code.
# Chạy đầy đủ (install + run app) là path NẶNG, xem mục "Run thật" cuối file.
set -euo pipefail
cd "$(dirname "$0")"

PY="$(command -v python3 || command -v python)"
EXCLUDE='(^|/)(\.?venv|env|node_modules|build|dist|__pycache__|\.git)(/|$)'

echo "=== [1/3] Syntax-check simple_ai_vision ==="
"$PY" -m compileall -q -x "$EXCLUDE" simple_ai_vision

echo "=== [2/3] Syntax-check fall_detection_web + services ==="
"$PY" -m compileall -q -x "$EXCLUDE" fall_detection_web services

echo "=== [3/3] Validate docker-compose configs (nếu có docker) ==="
if command -v docker >/dev/null 2>&1; then
  for f in docker-compose.yml docker-compose.prod.yml; do
    [ -f "$f" ] && docker compose -f "$f" config -q && echo "  ok: $f"
  done
else
  echo "  (bỏ qua — không có docker CLI ở môi trường này)"
fi

echo ""
echo "=== Verify NHANH xong ==="
echo "Next steps:"
echo "  1. Đọc feature_list.json → chọn 1 feature in-progress (hiện: feat-007)"
echo "  2. Đọc progress.md để lấy ngữ cảnh phiên trước"
echo "  3. Chỉ sửa file thuộc feature đang làm"
echo "  4. Verify trước khi claim done (xem Definition of Done trong CLAUDE.md)"
echo ""
echo "--- Run thật (path nặng, theo nhu cầu) ---"
echo "  simple_ai_vision : cd simple_ai_vision && pip install -r requirements.txt && uvicorn app:app --port 8000"
echo "  fall_detection_web: cd fall_detection_web && pip install -r requirements.txt && uvicorn app:app --port 8090   # admin/admin"
echo "  full stack (dev) : docker compose up -d  →  http://localhost:8090"
echo "  prod             : ssh -i ~/.ssh/id_ed25519_flow_demo ubuntu@163.227.121.206  (rsync→rebuild→up -d)"
