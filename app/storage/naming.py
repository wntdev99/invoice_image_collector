"""Filename composition rules.

Format: ``{label}_{YYYYMMDD}_{HHMMSSmmm}.{ext}``

Labels are sanitized to keep only safe filename characters
(ASCII alphanumeric, Korean Hangul, underscore, hyphen) and clamped to 64
characters. Empty / whitespace-only labels fall back to ``capture``.
"""
from __future__ import annotations

import re
from datetime import datetime


SUPPORTED_EXTENSIONS = ("png", "jpg", "webp")
DEFAULT_EXTENSION = "png"
DEFAULT_LABEL = "capture"

_LABEL_SANITIZE = re.compile(r"[^A-Za-z0-9_\-가-힣]")
_MAX_LABEL_LEN = 64


def sanitize_label(label: str | None) -> str:
    if label is None:
        return DEFAULT_LABEL
    stripped = label.strip()
    if not stripped:
        return DEFAULT_LABEL
    cleaned = _LABEL_SANITIZE.sub("_", stripped)
    return cleaned[:_MAX_LABEL_LEN] or DEFAULT_LABEL


def normalize_extension(ext: str | None) -> str:
    if ext is None:
        return DEFAULT_EXTENSION
    e = ext.strip().lower().lstrip(".")
    if e == "jpeg":
        e = "jpg"
    if e not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"unsupported extension: {ext!r} (supported: {SUPPORTED_EXTENSIONS})"
        )
    return e


def compose_filename(label: str | None, ext: str | None, now: datetime | None = None) -> str:
    when = now or datetime.now()
    safe_label = sanitize_label(label)
    safe_ext = normalize_extension(ext)
    ms = when.microsecond // 1000
    timestamp = when.strftime("%Y%m%d_%H%M%S") + f"{ms:03d}"
    return f"{safe_label}_{timestamp}.{safe_ext}"
