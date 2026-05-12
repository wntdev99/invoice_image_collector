"""Camera-domain exceptions."""
from __future__ import annotations


class CameraError(Exception):
    """Base for camera-related errors."""


class CameraNotFound(CameraError):
    """The requested camera id is not registered."""


class CameraBusy(CameraError):
    """V4L2 device could not be opened (likely in use by another process)."""


class CameraDisabled(CameraError):
    """Camera is administratively disabled by the user (other app uses it)."""
