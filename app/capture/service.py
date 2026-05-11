"""CaptureService — orchestrates the reopen-at-full-res shutter pipeline.

Sequence on POST /api/cameras/{id}/capture:
  1. Force-close any active preview FrameSource (releases /dev/videoN).
  2. Open V4L2 device at the camera's max supported resolution.
  3. Burn a few warm-up frames so the new format settles.
  4. If the camera supports AF, wait for focus to stabilize.
  5. Read one frame; encode; write to storage_dir.
  6. Release the temporary device.
  7. Preview stream will be re-established by the client (image src re-set)
     so we don't reopen here.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

from app.camera.backends.v4l2 import preferred_format
from app.camera.errors import CameraBusy, CameraNotFound
from app.camera.models import Camera
from app.camera.registry import CameraRegistry
from app.capture.encoder import EncodingFailed, encode_image
from app.capture.focus_stabilizer import FocusStabilizer
from app.storage.naming import compose_filename, normalize_extension
from app.stream.coordinator import StreamCoordinator

if TYPE_CHECKING:
    import numpy as np


_log = logging.getLogger(__name__)

_WARMUP_FRAMES = 5


class CaptureFailed(Exception):
    """Could not read a frame from the temporary full-res device."""


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
        self._stabilizer = FocusStabilizer()
        self._lock = asyncio.Lock()
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    async def capture(self, camera_id: str, label: str | None, ext: str | None) -> CaptureResult:
        async with self._lock:
            camera = self._registry.get(camera_id)
            if camera is None:
                raise CameraNotFound(camera_id)

            safe_ext = normalize_extension(ext)
            filename = compose_filename(label, safe_ext)
            path = self._storage_dir / filename

            # Vacate the V4L2 device so we can reopen at full resolution.
            await self._coordinator.force_close_active()

            frame, resolution = await asyncio.to_thread(self._capture_oneshot, camera)
            if frame is None:
                raise CaptureFailed(
                    f"capture failed: no frame read at {resolution[0]}x{resolution[1]}"
                )

            data = encode_image(frame, safe_ext)
            await asyncio.to_thread(path.write_bytes, data)
            _log.info(
                "capture saved: %s (%d bytes, %dx%d)",
                filename, len(data), resolution[0], resolution[1],
            )
            return CaptureResult(
                filename=filename,
                size=len(data),
                path=str(path),
                resolution=resolution,
            )

    def _capture_oneshot(
        self, camera: Camera
    ) -> "tuple[np.ndarray | None, tuple[int, int]]":
        max_res = max(
            camera.capabilities.resolutions, key=lambda r: r[0] * r[1]
        ) if camera.capabilities.resolutions else (1920, 1080)
        fourcc = preferred_format(camera.capabilities.formats)

        cap = cv2.VideoCapture(camera.device_path, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CameraBusy(
                f"failed to open V4L2 device {camera.device_path} for capture"
            )
        try:
            if fourcc:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, max_res[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, max_res[1])
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            _log.info(
                "capture: reopened %s at %dx%d (requested %dx%d)",
                camera.device_path, actual_w, actual_h, max_res[0], max_res[1],
            )

            if (
                camera.capabilities.has_autofocus
                and camera.capabilities.focus is not None
            ):
                self._stabilizer.wait_stable(cap, camera.capabilities.focus)
            else:
                for _ in range(_WARMUP_FRAMES):
                    cap.read()

            ok, frame = cap.read()
            return (frame if ok else None), (actual_w, actual_h)
        finally:
            cap.release()
