"""CaptureService — saves the latest streamed frame.

The active FrameSource is already running at the user-selected resolution
with auto-exposure / auto-WB / focus settled, so capture is simply
"snapshot the most recent BGR frame, encode, write." No device reopen,
no warmup, no AE wait — the camera state is whatever the user is seeing.

If the camera has no active stream (user is not viewing the camera page),
returns 409 — the client should open /cam/{id} first.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from app.camera.errors import CameraNotFound
from app.camera.registry import CameraRegistry
from app.capture.encoder import encode_image
from app.storage.naming import compose_filename, normalize_extension
from app.stream.coordinator import StreamCoordinator


_log = logging.getLogger(__name__)


class CaptureFailed(Exception):
    """No frame is available from the active stream."""


class StreamNotActive(Exception):
    """The camera has no active streaming source to snapshot from."""


@dataclass(frozen=True, slots=True)
class CaptureResult:
    filename: str
    size: int
    path: str
    resolution: tuple[int, int]


class CaptureService:
    def __init__(
        self,
        registry: CameraRegistry,
        coordinator: StreamCoordinator,
        storage_dir: Path,
    ) -> None:
        self._registry = registry
        self._coordinator = coordinator
        self._storage_dir = storage_dir
        self._lock = asyncio.Lock()
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    async def capture(self, camera_id: str, label: str | None, ext: str | None) -> CaptureResult:
        async with self._lock:
            camera = self._registry.get(camera_id)
            if camera is None:
                raise CameraNotFound(camera_id)

            source = self._coordinator.get_active_source_for(camera_id)
            if source is None:
                raise StreamNotActive(
                    "camera is not currently streaming; open /cam/{id} first"
                )

            frame = source.get_latest_frame()
            if frame is None:
                raise CaptureFailed("no frame available yet from active stream")
            height, width = frame.shape[:2]

            safe_ext = normalize_extension(ext)
            filename = compose_filename(label, safe_ext)
            path = self._storage_dir / filename

            data = await asyncio.to_thread(encode_image, frame, safe_ext)
            await asyncio.to_thread(path.write_bytes, data)
            _log.info(
                "capture saved: %s (%d bytes, %dx%d)",
                filename, len(data), width, height,
            )
            return CaptureResult(
                filename=filename,
                size=len(data),
                path=str(path),
                resolution=(width, height),
            )
