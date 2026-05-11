#!/usr/bin/env bash
#
# setup_arducam_udev.sh
#
# Arducam B0478 (USB3 48MP IMX586, USB ID 04b4:0478) 용 udev 규칙 설치
#
# 효과:
#   1) V4L2 캡처 노드(index 0)에 MODE=0666, GROUP=video 부여
#   2) 카메라 시리얼 기반 심볼릭 링크 생성: /dev/arducam_<serial-tail>
#   3) USB raw 디바이스 노드에 MODE=0666, GROUP=video 부여 (USBDEVFS_RESET 용)
#
# 다중 카메라 시 시리얼이 유일하지 않으면 SYMLINK 충돌이 발생하므로
# 설치 전 자동 검증 절차를 포함한다.

set -euo pipefail

RULE_FILE="/etc/udev/rules.d/99-arducam-imx586.rules"
VENDOR_ID="04b4"
PRODUCT_ID="0478"

# ---------------------------------------------------------------------------
# 로깅 헬퍼
# ---------------------------------------------------------------------------
info() { printf '[+] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
err()  { printf '[x] %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1) 사전 점검
# ---------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || err "root 권한이 필요합니다. 'sudo $0' 로 실행하세요."

command -v udevadm >/dev/null 2>&1 || err "udevadm을 찾을 수 없습니다. systemd-udev가 설치되어 있어야 합니다."

# ---------------------------------------------------------------------------
# 2) 연결된 Arducam 카메라 탐지 + 시리얼 유일성 검증
# ---------------------------------------------------------------------------
info "Arducam B0478 (${VENDOR_ID}:${PRODUCT_ID}) 탐색"

declare -a FOUND=()
for d in /sys/bus/usb/devices/*; do
  [ -f "$d/idVendor"  ] || continue
  [ -f "$d/idProduct" ] || continue
  [ "$(cat "$d/idVendor")"  = "$VENDOR_ID"  ] || continue
  [ "$(cat "$d/idProduct")" = "$PRODUCT_ID" ] || continue
  FOUND+=("$d")
done

if [ ${#FOUND[@]} -eq 0 ]; then
  warn "현재 연결된 Arducam B0478 카메라가 없습니다."
  warn "규칙은 설치되지만 시리얼 유일성 검증은 건너뜁니다."
else
  info "${#FOUND[@]}개 카메라 발견"
  declare -A SERIAL_COUNT=()
  for d in "${FOUND[@]}"; do
    name=$(basename "$d")
    if [ -f "$d/serial" ]; then
      s=$(cat "$d/serial")
      SERIAL_COUNT["$s"]=$(( ${SERIAL_COUNT["$s"]:-0} + 1 ))
      info "  - $name : serial=$s"
    else
      warn "  - $name : serial 속성 없음 (방안 A 부적합)"
      SERIAL_COUNT["__missing__"]=$(( ${SERIAL_COUNT["__missing__"]:-0} + 1 ))
    fi
  done

  dup=0
  for s in "${!SERIAL_COUNT[@]}"; do
    if [ "${SERIAL_COUNT[$s]}" -gt 1 ]; then
      warn "시리얼 '$s' 중복: ${SERIAL_COUNT[$s]}개 카메라"
      dup=1
    fi
  done

  if [ "$dup" -eq 1 ]; then
    warn "시리얼 충돌이 있어 SYMLINK가 한 카메라에만 살아남습니다."
    warn "포트 기반(by-path) 또는 순차 슬롯 방식 사용을 권장합니다."
    if [ -t 0 ]; then
      printf '계속 설치하시겠습니까? [y/N] '
      read -r ans
      case "$ans" in
        y|Y|yes|YES) : ;;
        *) err "사용자에 의해 중단되었습니다." ;;
      esac
    else
      err "비대화 모드에서 시리얼 중복 감지 — 중단합니다."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 3) 기존 규칙 백업
# ---------------------------------------------------------------------------
if [ -f "$RULE_FILE" ]; then
  backup="${RULE_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
  cp -a "$RULE_FILE" "$backup"
  info "기존 규칙 백업: $backup"
fi

# ---------------------------------------------------------------------------
# 4) 규칙 작성
#
# PROGRAM은 udev가 $env{ID_SERIAL_SHORT}를 먼저 치환한 뒤 /bin/sh로 실행한다.
# 결과 stdout이 %c로 SYMLINK에 삽입된다. "Arducam_" 접두어를 제거해
# /dev/arducam_Arducam_... 같은 이중 표기를 피한다.
# ---------------------------------------------------------------------------
info "udev 규칙 작성: $RULE_FILE"
cat > "$RULE_FILE" <<'EOF'
# Arducam B0478 (USB3 48MP IMX586) — managed by setup_arducam_udev.sh

# V4L2 캡처 노드 (index 0): 권한·그룹 + 시리얼 기반 SYMLINK
SUBSYSTEM=="video4linux", ATTRS{idVendor}=="04b4", ATTRS{idProduct}=="0478", \
  ATTR{index}=="0", MODE="0666", GROUP="video", \
  PROGRAM="/bin/sh -c 'echo $env{ID_SERIAL_SHORT} | sed s/^Arducam_//'", \
  SYMLINK+="arducam_%c"

# USB raw 디바이스 노드: 권한·그룹 (USBDEVFS_RESET 용)
SUBSYSTEM=="usb", ATTRS{idVendor}=="04b4", ATTRS{idProduct}=="0478", \
  MODE="0666", GROUP="video"
EOF
chmod 0644 "$RULE_FILE"

# ---------------------------------------------------------------------------
# 5) udev reload & trigger
# ---------------------------------------------------------------------------
info "udev 규칙 reload"
udevadm control --reload-rules

info "udev 트리거 (이미 연결된 카메라에 즉시 적용)"
udevadm trigger --subsystem-match=video4linux --action=change || true
udevadm trigger --subsystem-match=usb --action=change \
  --attr-match=idVendor="$VENDOR_ID" --attr-match=idProduct="$PRODUCT_ID" || true
udevadm settle --timeout=10 || true

# ---------------------------------------------------------------------------
# 6) 결과 검증
# ---------------------------------------------------------------------------
info "결과 — /dev/arducam_* 심볼릭 링크:"
if compgen -G "/dev/arducam_*" >/dev/null; then
  ls -la /dev/arducam_*
else
  warn "  심볼릭 링크가 생성되지 않았습니다."
  warn "  - 카메라가 실제로 연결되어 있는지 확인하세요."
  warn "  - 또는 카메라를 한 번 뽑았다 다시 꽂으면 적용됩니다."
fi

echo
info "결과 — /dev/video* 권한:"
ls -la /dev/video* 2>/dev/null || warn "  /dev/video* 노드가 없습니다."

echo
info "설치 완료"
