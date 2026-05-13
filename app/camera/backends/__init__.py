"""Camera capture backends.

Dispatches camera.device_path to the appropriate backend:
  - ``/dev/videoN``     → V4L2CaptureDevice (USB UVC)
  - ``wgwk://<host>``   → WgwkCaptureDevice (WGWK-AS500J IP camera)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.camera.backends.v4l2 import V4L2CaptureDevice

if TYPE_CHECKING:
    from app.camera.models import Camera


def make_capture_device(camera: "Camera"):
    """Return a capture device matching ``camera.device_path``.

    Backends implement the same minimal interface as ``V4L2CaptureDevice``
    (open / read / release / get_focus / set_focus / get_autofocus /
    set_autofocus / get_power_line_frequency / set_power_line_frequency)
    so ``FrameSource`` and ``CameraController`` are backend-agnostic.
    """
    path = camera.device_path
    if path.startswith("wgwk://"):
        from app.camera.backends.wgwk import WgwkCaptureDevice, get_config
        return WgwkCaptureDevice(get_config(camera.id))
    return V4L2CaptureDevice(path)


__all__ = ["V4L2CaptureDevice", "make_capture_device"]
