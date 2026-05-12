"""StreamCoordinator — enforces the single-active-camera policy.

Holds at most one FrameSource. ``acquire`` switches to the requested camera
(closing the previous source if a different camera is requested). ``release``
decrements a refcount; when it hits zero, the active source is closed.

Per-camera preferred resolution (set by the user via PUT /stream-config) is
remembered in memory and applied on next ``acquire`` for that camera.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.camera.errors import CameraDisabled, CameraNotFound
from app.camera.frame_source import FrameSource
from app.camera.registry import CameraRegistry


_log = logging.getLogger(__name__)

_DEFAULT_RESOLUTION: tuple[int, int] = (1280, 720)


class StreamCoordinator:
    def __init__(self, registry: CameraRegistry) -> None:
        self._registry = registry
        self._lock = asyncio.Lock()
        self._active_id: str | None = None
        self._active_source: FrameSource | None = None
        self._refcount = 0
        # Per-camera preferred preview resolution, set by user via stream-config.
        self._preferred_resolution: dict[str, tuple[int, int]] = {}

    def get_active_source_for(self, camera_id: str) -> FrameSource | None:
        """Read-only snapshot — used by /controls and /capture. Race window OK."""
        if self._active_id == camera_id:
            return self._active_source
        return None

    def get_preferred_resolution(self, camera_id: str) -> tuple[int, int] | None:
        return self._preferred_resolution.get(camera_id)

    def _resolution_for(self, camera_id: str) -> tuple[int, int]:
        return self._preferred_resolution.get(camera_id, _DEFAULT_RESOLUTION)

    async def change_resolution(self, camera_id: str, width: int, height: int) -> None:
        """Set the preferred preview resolution for ``camera_id``.

        If that camera is currently active, tear it down so the next acquire
        (driven by the client re-setting img.src) opens at the new size.
        """
        async with self._lock:
            self._preferred_resolution[camera_id] = (width, height)
            _log.info("stream: preferred resolution id=%s set to %dx%d",
                      camera_id, width, height)
            if self._active_id == camera_id and self._active_source is not None:
                _log.info("stream: tearing down active source to apply new resolution")
                self._active_source.close()
                self._active_source = None
                self._active_id = None
                self._refcount = 0

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
            if self._registry.is_disabled(camera_id):
                raise CameraDisabled(camera_id)

            resolution = self._resolution_for(camera_id)
            source = FrameSource(camera, loop, target_resolution=resolution)
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

    async def force_close_active_if(self, camera_id: str) -> bool:
        """Close the active source only if it matches ``camera_id``.

        Used when the user disables a camera that is currently streaming —
        we vacate the V4L2 handle so another app can claim it immediately.
        Returns True if a source was closed, False if no match.
        """
        async with self._lock:
            if self._active_id != camera_id or self._active_source is None:
                return False
            _log.info("stream: closing active id=%s (camera disabled)", camera_id)
            self._active_source.close()
            self._active_source = None
            self._active_id = None
            self._refcount = 0
            return True

    async def shutdown(self) -> None:
        async with self._lock:
            if self._active_source is not None:
                self._active_source.close()
                self._active_source = None
                self._active_id = None
                self._refcount = 0
