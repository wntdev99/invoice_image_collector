"""Thread-safe in-memory registry of currently attached cameras.

Also keeps track of which camera ids the user has administratively disabled
(e.g. because another application — ROS, vendor SDK — needs exclusive V4L2
access). Disabled-id set is persisted to a JSON file so the choice survives
service restarts.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.camera.models import Camera


_log = logging.getLogger(__name__)


class CameraRegistry:
    def __init__(self, disabled_state_path: Path | None = None) -> None:
        self._cameras: dict[str, Camera] = {}
        self._lock = threading.Lock()
        self._disabled_ids: set[str] = set()
        self._disabled_state_path = disabled_state_path
        if self._disabled_state_path is not None:
            self._load_disabled()

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

    # ----- disabled state -----

    def is_disabled(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._disabled_ids

    def set_disabled(self, camera_id: str, disabled: bool) -> bool:
        """Returns True if state actually changed."""
        with self._lock:
            present = camera_id in self._disabled_ids
            if disabled and not present:
                self._disabled_ids.add(camera_id)
                changed = True
            elif not disabled and present:
                self._disabled_ids.discard(camera_id)
                changed = True
            else:
                changed = False
        if changed:
            self._save_disabled()
            _log.info("registry: camera %s %s",
                      camera_id, "disabled" if disabled else "enabled")
        return changed

    def _load_disabled(self) -> None:
        assert self._disabled_state_path is not None
        if not self._disabled_state_path.exists():
            return
        try:
            data = json.loads(self._disabled_state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("registry: could not read disabled state: %s", exc)
            return
        ids = data.get("disabled", [])
        if isinstance(ids, list):
            with self._lock:
                self._disabled_ids = {str(x) for x in ids}
            _log.info("registry: loaded %d disabled id(s) from %s",
                      len(self._disabled_ids), self._disabled_state_path)

    def _save_disabled(self) -> None:
        if self._disabled_state_path is None:
            return
        try:
            self._disabled_state_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {"disabled": sorted(self._disabled_ids)}
            tmp = self._disabled_state_path.with_suffix(
                self._disabled_state_path.suffix + ".tmp"
            )
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self._disabled_state_path)
        except OSError as exc:
            _log.warning("registry: could not save disabled state: %s", exc)
