"""Stream-domain exceptions."""
from __future__ import annotations


class StreamError(Exception):
    """Base for stream-related errors."""


class StreamClosed(StreamError):
    """The FrameSource was closed while a consumer was waiting for a frame."""
