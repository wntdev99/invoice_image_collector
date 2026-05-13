"""Camera domain model. Pure data — no I/O, no V4L2 specifics."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FocusRange:
    min: int
    max: int
    step: int
    default: int


@dataclass(frozen=True, slots=True)
class ZoomRange:
    """Optical zoom 컨트롤 범위 + 동작 모드.

    ``mode``:
      - ``"absolute"``: 슬라이더 UI. 카메라가 절대 위치를 readback할 수 있을 때
        (V4L2 ``zoom_absolute``, Logitech PTZ Pro 등).
      - ``"relative"``: -/+ 버튼 UI (press-and-hold continuous motion). 카메라가
        절대 위치 readback이 불가하거나 모터 시간 기반 제어일 때 (WGWK-AS500J).

    ``min``/``max``/``step``/``default``는 absolute 모드에서 슬라이더 범위로
    사용되고, relative 모드에서는 OSD 표시 또는 추정값 참고용으로만 의미.
    """
    min: int
    max: int
    step: int
    default: int
    mode: str = "absolute"


@dataclass(frozen=True, slots=True)
class PowerLineFrequency:
    """V4L2 ``power_line_frequency`` menu control range + options."""
    min: int
    max: int
    default: int
    options: tuple[tuple[int, str], ...]   # ((0, "Disabled"), (1, "50 Hz"), (2, "60 Hz"))


@dataclass(frozen=True, slots=True)
class Capabilities:
    has_autofocus: bool        # exposes V4L2 AF/MF mode toggle
    has_manual_focus: bool     # exposes focus_absolute (or equivalent) control
    focus: FocusRange | None = None
    zoom: ZoomRange | None = None     # optical zoom (V4L2 zoom_absolute or IP zoom)
    power_line_frequency: PowerLineFrequency | None = None
    formats: tuple[str, ...] = ()
    resolutions: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Camera:
    id: str
    device_path: str
    name: str
    vendor_id: str
    product_id: str
    serial: str | None
    bus_path: str | None
    capabilities: Capabilities
