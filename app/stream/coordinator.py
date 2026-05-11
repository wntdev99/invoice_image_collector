"""StreamCoordinator — enforces the single-active-camera policy.

Holds at most one FrameSource. ``acquire`` switches to the requested camera
(closing the previous source if a different camera is requested). ``release``
decrements a refcount; when it hits zero, the active source is closed.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.camera.errors import CameraNotFound
from app.camera.frame_source import FrameSource
from app.camera.registry import CameraRegistry


_log = logging.getLogger(__name__)


class StreamCoordinator:
    def __init__(self, registry: CameraRegistry) -> None:
        self._registry = registry
        self._lock = asyncio.Lock()
        self._active_id: str | None = None
        self._active_source: FrameSource | None = None
        self._refcount = 0

    def get_active_source_for(self, camera_id: str) -> FrameSource | None:
        """Read-only snapshot — used by /controls endpoints. Race window OK."""
        if self._active_id == camera_id:
            return self._active_source
        return None

    async def acquire(self, camera_id: str, loop: asyncio.AbstractEventLoop) -> FrameSource:
        async with self._lock:
            # Same camera already streaming → share it
            if self._active_id == camera_id and self._active_source is not None:
                self._refcount += 1
                _log.info("stream: reuse active source id=%s refcount=%d",
                          camera_id, self._refcount)
                return self._active_source

            # Different (or none) active → close previous, open new
            if self._active_source is not None:
                _log.info("stream: switching from id=%s to id=%s",
                          self._active_id, camera_id)
                self._active_source.close()
                self._active_source = None
                self._active_id = None
                self._refcount = 0

            camera = self._registry.get(camera_id)
            if camera is None:
                raise CameraNotFound(camera_id)

            source = FrameSource(camera, loop)
            await source.start()
            self._active_source = source
            self._active_id = camera_id
            self._refcount = 1
            _log.info("stream: opened id=%s refcount=1 negotiated=%s",
                      camera_id, source.negotiated)
            return source

    async def release(self, camera_id: str, source: FrameSource) -> None:
        async with self._lock:
            # If the source is no longer the active one, it's already closed
            # by a switch — nothing to do.
            if source is not self._active_source:
                return
            self._refcount -= 1
            _log.info("stream: release id=%s refcount=%d", camera_id, self._refcount)
            if self._refcount <= 0:
                self._active_source.close()
                self._active_source = None
                self._active_id = None
                self._refcount = 0

    @asynccontextmanager
    async def stream_session(
        self, camera_id: str, loop: asyncio.AbstractEventLoop
    ) -> AsyncIterator[FrameSource]:
        source = await self.acquire(camera_id, loop)
        try:
            yield source
        finally:
            await self.release(camera_id, source)

    async def force_close_active(self) -> str | None:
        """Force-close the current active source (used by capture before reopen).

        Returns the camera id that was closed, or ``None`` if nothing was active.
        Any MJPEG generator on that source will observe StreamClosed on its
        next ``wait_frame_after`` call and end gracefully.
        """
        async with self._lock:
            if self._active_source is None:
                return None
            prev_id = self._active_id
            _log.info("stream: force-close for capture id=%s", prev_id)
            self._active_source.close()
            self._active_source = None
            self._active_id = None
            self._refcount = 0
            return prev_id

    async def shutdown(self) -> None:
        async with self._lock:
            if self._active_source is not None:
                self._active_source.close()
                self._active_source = None
                self._active_id = None
                self._refcount = 0
