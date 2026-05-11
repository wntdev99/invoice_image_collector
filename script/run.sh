#!/usr/bin/env bash
#
# run.sh
#
# uvicorn으로 invoice_image_collector 서버 실행.
#
# 환경변수:
#   IIC_HOST   listen host (기본 0.0.0.0)
#   IIC_PORT   listen port (기본 8001)
#
# 추가 인자는 uvicorn에 그대로 전달됩니다 (예: --reload).
#
# 사용 예:
#   ./script/run.sh
#   IIC_PORT=8002 ./script/run.sh
#   ./script/run.sh --reload

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="$PROJECT_ROOT/.venv"
if [ ! -x "$VENV_DIR/bin/uvicorn" ]; then
  echo "[x] .venv 또는 의존성 미설치." >&2
  echo "    먼저: $SCRIPT_DIR/setup.sh" >&2
  exit 1
fi

HOST="${IIC_HOST:-0.0.0.0}"
PORT="${IIC_PORT:-8001}"

exec "$VENV_DIR/bin/uvicorn" app.main:app \
  --host "$HOST" --port "$PORT" "$@"
