"""MJPEG over HTTP multipart/x-mixed-replace."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import cv2

from app.camera.frame_source import FrameSource
from app.stream.errors import StreamClosed

if TYPE_CHECKING:
    import numpy as np


_log = logging.getLogger(__name__)

_BOUNDARY = "frame"


class MJPEGStreamProvider:
    media_type = f"multipart/x-mixed-replace; boundary={_BOUNDARY}"

    def __init__(self, quality: int = 85) -> None:
        self._quality = quality

    async def iter_payloads(self, source: FrameSource) -> AsyncIterator[bytes]:
        last_seq = 0
        while True:
            try:
                seq, frame = await source.wait_frame_after(last_seq)
            except StreamClosed:
                _log.info("mjpeg: source closed, ending stream")
                return
            last_seq = seq
            jpeg = await asyncio.to_thread(self._encode_jpeg, frame)
            if not jpeg:
                continue
            yield (
                b"--" + _BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                + jpeg + b"\r\n"
            )

    def _encode_jpeg(self, frame: "np.ndarray") -> bytes:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        if not ok:
            return b""
        return buf.tobytes()
