"""Application configuration. Single source of truth for runtime constants."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    host: str
    port: int
    storage_dir: Path


def _default_storage_dir() -> Path:
    return Path.home() / "Pictures" / "invoice_image_collector"


def load_settings() -> Settings:
    return Settings(
        app_name="Invoice Image Collector",
        host=os.getenv("IIC_HOST", "0.0.0.0"),
        port=int(os.getenv("IIC_PORT", "8000")),
        storage_dir=Path(os.getenv("IIC_STORAGE_DIR", str(_default_storage_dir()))),
    )


settings = load_settings()
