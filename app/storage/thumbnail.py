"""On-the-fly thumbnail generation with a small (path, mtime) cache.

Thumbnails are bounded so the longest edge is ``THUMB_MAX_SIZE`` pixels
and encoded as JPEG quality 75 (smaller cache entries vs. visible quality
tradeoff). The cache key includes mtime so editing/replacing a file
naturally invalidates the cached thumbnail.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path

import cv2


_log = logging.getLogger(__name__)

THUMB_MAX_SIZE = 240
_JPEG_QUALITY = 75


def _render(image_path: Path) -> bytes:
    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"cannot decode image: {image_path}")
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest > THUMB_MAX_SIZE:
        scale = THUMB_MAX_SIZE / longest
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise RuntimeError(f"thumbnail encode failed: {image_path}")
    return buf.tobytes()


class ThumbnailCache:
    def __init__(self, max_entries: int = 256) -> None:
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[str, float], bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: Path) -> bytes:
        mtime = path.stat().st_mtime
        key = (str(path), mtime)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        data = _render(path)
        with self._lock:
            self._cache[key] = data
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return data
