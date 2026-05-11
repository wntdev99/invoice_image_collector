"""CameraController — thin facade over V4L2CaptureDevice for ctrl access.

Bound to one open device. Exposes only the ctrls our UI cares about
(focus_absolute and autofocus toggle). Returns ``None`` for unsupported
ctrls based on Capabilities, so callers don't have to special-case
"unsupported" vs "set failed".
"""
from __future__ import annotations

from app.camera.backends.v4l2 import V4L2CaptureDevice
from app.camera.models import Capabilities


class CameraController:
    def __init__(self, device: V4L2CaptureDevice, capabilities: Capabilities) -> None:
        self._device = device
        self._caps = capabilities

    def snapshot(self) -> dict:
        out: dict = {}
        if self._caps.has_manual_focus and self._caps.focus is not None:
            current = self._device.get_focus()
            out["focus"] = {
                "value": current,
                "min": self._caps.focus.min,
                "max": self._caps.focus.max,
                "step": self._caps.focus.step,
                "default": self._caps.focus.default,
            }
        out["autofocus"] = {
            "supported": self._caps.has_autofocus,
            "enabled": self._device.get_autofocus() if self._caps.has_autofocus else None,
        }
        if self._caps.power_line_frequency is not None:
            plf = self._caps.power_line_frequency
            out["power_line_frequency"] = {
                "value": self._device.get_power_line_frequency(),
                "min": plf.min,
                "max": plf.max,
                "default": plf.default,
                "options": [{"value": v, "label": label} for v, label in plf.options],
            }
        return out

    def set_focus(self, value: int) -> int | None:
        if not self._caps.has_manual_focus or self._caps.focus is None:
            return None
        clamped = max(self._caps.focus.min, min(self._caps.focus.max, value))
        return self._device.set_focus(clamped)

    def set_autofocus(self, enabled: bool) -> bool | None:
        if not self._caps.has_autofocus:
            return None
        return self._device.set_autofocus(enabled)

    def set_power_line_frequency(self, value: int) -> int | None:
        if self._caps.power_line_frequency is None:
            return None
        plf = self._caps.power_line_frequency
        clamped = max(plf.min, min(plf.max, value))
        return self._device.set_power_line_frequency(clamped)
