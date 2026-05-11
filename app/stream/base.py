"""StreamProvider interface — swap MJPEG for WebRTC etc. without touching the rest."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.camera.frame_source import FrameSource


class StreamProvider(Protocol):
    """Encodes frames from a FrameSource into a byte stream suitable for HTTP response."""

    media_type: str

    def iter_payloads(self, source: FrameSource) -> AsyncIterator[bytes]: ...
