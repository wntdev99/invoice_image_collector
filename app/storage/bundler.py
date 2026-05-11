"""Zip selected image files for download.

Uses ``ZIP_STORED`` (no compression) — JPEG/WebP/PNG are already
compressed, so a second compression pass would only burn CPU. Builds
the archive in a single in-memory buffer; for the typical session
(<= ~200 images) this is fine. Switch to a streaming writer if galleries
grow very large.
"""
from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path


_log = logging.getLogger(__name__)


def build_zip(paths: Iterable[Path]) -> bytes:
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for path in paths:
            if not path.is_file():
                continue
            zf.write(path, arcname=path.name)
            count += 1
    _log.info("zip built: %d entries, %d bytes", count, buf.tell())
    return buf.getvalue()


def stream_zip(paths: Iterable[Path], chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    data = build_zip(paths)
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]
