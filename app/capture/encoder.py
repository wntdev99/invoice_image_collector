"""Frame-to-bytes encoding for the supported still-image extensions."""
from __future__ import annotations

from typing import TYPE_CHECKING

import cv2

if TYPE_CHECKING:
    import numpy as np


class EncodingFailed(Exception):
    """cv2.imencode returned False."""


_JPEG_QUALITY = 95
_WEBP_QUALITY = 95


def encode_image(frame: "np.ndarray", ext: str) -> bytes:
    """``ext`` must be one of ``app.storage.naming.SUPPORTED_EXTENSIONS``."""
    if ext == "jpg":
        params = [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY]
        cv2_ext = ".jpg"
    elif ext == "webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, _WEBP_QUALITY]
        cv2_ext = ".webp"
    else:
        raise ValueError(f"unsupported extension: {ext}")

    ok, buf = cv2.imencode(cv2_ext, frame, params)
    if not ok:
        raise EncodingFailed(f"cv2.imencode failed for {ext}")
    return buf.tobytes()
