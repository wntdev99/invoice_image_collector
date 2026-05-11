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
