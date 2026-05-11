"""Camera-domain events published on the EventBus."""
from __future__ import annotations

from dataclasses import dataclass

from app.camera.models import Camera


@dataclass(frozen=True, slots=True)
class CameraAttached:
    camera: Camera


@dataclass(frozen=True, slots=True)
class CameraDetached:
    camera_id: str
