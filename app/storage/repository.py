"""On-disk image enumeration / lookup / delete.

Path resolution is hardened against traversal: anything containing a path
separator, leading dot, or resolving outside ``storage_dir`` is rejected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path


_log = logging.getLogger(__name__)

# Includes png so any legacy files left by step 5 are still visible.
_ALLOWED_SUFFIXES = frozenset({"jpg", "jpeg", "png", "webp"})


@dataclass(frozen=True, slots=True)
class ImageMeta:
    name: str
    size: int
    mtime: float  # unix epoch seconds


class ImageRepository:
    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = storage_dir.resolve()
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    def list(self) -> list[ImageMeta]:
        items: list[ImageMeta] = []
        for p in self._storage_dir.iterdir():
            if not p.is_file():
                continue
            suffix = p.suffix.lower().lstrip(".")
            if suffix not in _ALLOWED_SUFFIXES:
                continue
            st = p.stat()
            items.append(ImageMeta(name=p.name, size=st.st_size, mtime=st.st_mtime))
        items.sort(key=lambda m: m.mtime, reverse=True)
        return items

    def path_of(self, name: str) -> Path | None:
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        candidate = (self._storage_dir / name).resolve()
        try:
            candidate.relative_to(self._storage_dir)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def delete(self, names: list[str]) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for name in names:
            p = self.path_of(name)
            if p is None:
                result[name] = False
                continue
            try:
                p.unlink()
                result[name] = True
                _log.info("image deleted: %s", name)
            except OSError as exc:
                _log.warning("image delete failed: %s — %s", name, exc)
                result[name] = False
        return result
