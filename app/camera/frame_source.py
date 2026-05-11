"""FrameSource — capture loop + latest-frame fan-out.

Runs the V4L2 read loop in a background thread (OpenCV's VideoCapture.read
blocks). Latest frame is exposed atomically and waiters are woken via an
asyncio.Event pulse from the event loop thread.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from app.camera.backends.v4l2 import (
    V4L2CaptureDevice,
    best_match_resolution,
    preferred_format,
)
from app.camera.models import Camera
from app.stream.errors import StreamClosed

if TYPE_CHECKING:
    import numpy as np


_log = logging.getLogger(__name__)


class FrameSource:
    """One physical camera being captured. Multiple consumers can wait on frames concurrently."""

    def __init__(
        self,
        camera: Camera,
        loop: asyncio.AbstractEventLoop,
        target_resolution: tuple[int, int] = (1280, 720),
        target_fps: int = 30,
    ) -> None:
        self._camera = camera
        self._loop = loop
        self._target_resolution = target_resolution
        self._target_fps = target_fps

        self._device = V4L2CaptureDevice(camera.device_path)
        self._capture_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

        self._latest_frame: "np.ndarray | None" = None
        self._latest_seq = 0
        self._new_frame_event = asyncio.Event()
        self._closed = False

    @property
    def camera(self) -> Camera:
        return self._camera

    @property
    def negotiated(self) -> tuple[int, int, float]:
        return self._device.negotiated

    async def start(self) -> None:
        width, height = best_match_resolution(
            self._camera.capabilities.resolutions, self._target_resolution
        )
        fourcc = preferred_format(self._camera.capabilities.formats)
        # Opening V4L2 can block briefly — run in executor
        await asyncio.to_thread(
            self._device.open, width, height, self._target_fps, fourcc
        )
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name=f"capture-{self._camera.id}",
            daemon=True,
        )
        self._capture_thread.start()

    def _capture_loop(self) -> None:
        consecutive_failures = 0
        while not self._stop_evt.is_set():
            ok, frame = self._device.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= 30:
                    _log.warning(
                        "capture loop: 30 consecutive read failures on %s, stopping",
                        self._camera.id,
                    )
                    break
                continue
            consecutive_failures = 0
            try:
                self._loop.call_soon_threadsafe(self._on_new_frame, frame)
            except RuntimeError:
                break  # event loop closed
        try:
            self._loop.call_soon_threadsafe(self._wake_all)
        except RuntimeError:
            pass

    def _on_new_frame(self, frame: "np.ndarray") -> None:
        # Runs in the event loop thread
        self._latest_frame = frame
        self._latest_seq += 1
        self._new_frame_event.set()
        self._new_frame_event.clear()

    def _wake_all(self) -> None:
        # Pulse the event to release any pending waiters
        self._new_frame_event.set()
        self._new_frame_event.clear()

    async def wait_frame_after(self, last_seq: int) -> "tuple[int, np.ndarray]":
        """Return (seq, frame) where seq > last_seq, or raise StreamClosed."""
        if self._closed:
            raise StreamClosed()
        if self._latest_seq > last_seq and self._latest_frame is not None:
            return self._latest_seq, self._latest_frame
        await self._new_frame_event.wait()
        if self._closed or self._latest_frame is None:
            raise StreamClosed()
        return self._latest_seq, self._latest_frame

    def close(self) -> None:
        """Synchronous close. Must be called from the event loop thread.

        Done synchronously because the typical caller (StreamCoordinator.release)
        runs inside a possibly-cancelled task (FastAPI cancels the response
        generator on client disconnect); any ``await`` in a cancelled finally
        block would raise CancelledError mid-cleanup and orphan the V4L2 handle.

        Releasing the device first unblocks any pending cv2.VideoCapture.read()
        so the capture thread exits its loop near-instantly.
        """
        if self._closed:
            return
        self._closed = True
        self._stop_evt.set()
        self._device.release()
        if self._capture_thread is not None:
            self._capture_thread.join(2.0)
            self._capture_thread = None
        self._wake_all()
