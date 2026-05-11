"""Shared Jinja2 templates instance for routers across the app."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates


WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))
