#!/usr/bin/env bash
#
# setup.sh
#
# invoice_image_collector 의존성·venv·환경 자동 설치
#
# 동작:
#   1) Python 3.11+ 확인
#   2) apt 의존성 설치 (v4l-utils, python3-venv)
#   3) video 그룹 소속 확인 (V4L2 디바이스 접근)
#   4) .venv 생성 + pip 업그레이드
#   5) pip install -e . (pyproject.toml의 모든 의존성)
#   6) 저장 디렉토리 생성
#
# 멱등하게 동작 (재실행 안전).
# 일반 사용자로 실행하세요. apt 단계에서만 sudo 비밀번호를 요구합니다.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

info() { printf '[+] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
err()  { printf '[x] %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1) 사전 점검
# ---------------------------------------------------------------------------
[ "$(id -u)" -ne 0 ] || err "root로 실행하지 마세요. 일반 사용자로 실행하면 apt 단계에서만 sudo 비밀번호를 받습니다."

[ -f "$PROJECT_ROOT/pyproject.toml" ] || err "pyproject.toml을 찾을 수 없습니다 ($PROJECT_ROOT)."

command -v python3 >/dev/null 2>&1 || err "python3을 찾을 수 없습니다."

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=${PY_VER%.*}
PY_MINOR=${PY_VER#*.}
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  err "Python 3.11+ 필요. 현재: $PY_VER"
fi
info "Python $PY_VER 확인"

# ---------------------------------------------------------------------------
# 2) apt 의존성
# ---------------------------------------------------------------------------
APT_PKGS=(v4l-utils python3-venv)
MISSING=()
for p in "${APT_PKGS[@]}"; do
  if ! dpkg -s "$p" >/dev/null 2>&1; then
    MISSING+=("$p")
  fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
  info "apt 패키지 설치: ${MISSING[*]}"
  sudo apt-get update
  sudo apt-get install -y "${MISSING[@]}"
else
  info "apt 의존성 이미 설치됨: ${APT_PKGS[*]}"
fi

# ---------------------------------------------------------------------------
# 3) video 그룹 (V4L2 디바이스 접근에 필요)
# ---------------------------------------------------------------------------
if id -nG "$USER" | tr ' ' '\n' | grep -qx video; then
  info "사용자 $USER 가 video 그룹에 속함"
else
  warn "사용자 $USER 가 video 그룹에 없습니다. 다음 명령으로 추가 후 재로그인 필요:"
  warn "  sudo usermod -aG video $USER"
fi

# ---------------------------------------------------------------------------
# 4) Python venv
# ---------------------------------------------------------------------------
VENV_DIR="$PROJECT_ROOT/.venv"
if [ -d "$VENV_DIR" ]; then
  info ".venv 이미 존재"
else
  info ".venv 생성"
  python3 -m venv "$VENV_DIR"
fi

# ---------------------------------------------------------------------------
# 5) pip + 의존성
# ---------------------------------------------------------------------------
info "pip 업그레이드"
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null

info "프로젝트 의존성 설치 (pip install -e .)"
"$VENV_DIR/bin/pip" install -e . >/dev/null

# ---------------------------------------------------------------------------
# 6) 저장 디렉토리
# ---------------------------------------------------------------------------
STORAGE_DIR="${IIC_STORAGE_DIR:-$HOME/Pictures/invoice_image_collector}"
if [ -d "$STORAGE_DIR" ]; then
  info "저장 디렉토리 이미 존재: $STORAGE_DIR"
else
  info "저장 디렉토리 생성: $STORAGE_DIR"
  mkdir -p "$STORAGE_DIR"
fi

# ---------------------------------------------------------------------------
# 7) 완료 + 다음 단계
# ---------------------------------------------------------------------------
echo
info "setup 완료"
echo
echo "다음 단계:"
echo "  1) (선택) Arducam B0478 udev 규칙:"
echo "       sudo $PROJECT_ROOT/script/setup_arducam_udev.sh"
echo
echo "  2) 서버 실행:"
echo "       $PROJECT_ROOT/script/run.sh"
echo
echo "  3) (선택) 부팅 시 자동 시작 — script/iic.service 안 주석 참조."
