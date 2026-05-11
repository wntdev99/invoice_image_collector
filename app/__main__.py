"""Entry point for `python -m app`.

Honors IIC_HOST / IIC_PORT env vars via app.config.settings. For direct
uvicorn CLI invocation, pass --host/--port instead — uvicorn does not
read this module's settings.
"""
from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
