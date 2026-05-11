"""Thread-safe in-memory registry of currently attached cameras."""
from __future__ import annotations

import threading

from app.camera.models import Camera


class CameraRegistry:
    def __init__(self) -> None:
        self._cameras: dict[str, Camera] = {}
        self._lock = threading.Lock()

    def add(self, camera: Camera) -> bool:
        with self._lock:
            if camera.id in self._cameras:
                return False
            self._cameras[camera.id] = camera
            return True

    def remove(self, camera_id: str) -> Camera | None:
        with self._lock:
            return self._cameras.pop(camera_id, None)

    def get(self, camera_id: str) -> Camera | None:
        with self._lock:
            return self._cameras.get(camera_id)

    def list(self) -> list[Camera]:
        with self._lock:
            return list(self._cameras.values())

    def find_by_device_path(self, device_path: str) -> Camera | None:
        with self._lock:
            for c in self._cameras.values():
                if c.device_path == device_path:
                    return c
            return None
