"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.autofocus import router as autofocus_router
from app.api.cameras import router as cameras_router
from app.api.capture import router as capture_router
from app.api.controls import router as controls_router
from app.api.events import router as events_router
from app.api.images import router as images_router
from app.api.stream import router as stream_router
from app.camera.discovery import CameraDiscovery
from app.camera.registry import CameraRegistry
from app.capture.autofocus import SoftwareAutofocus
from app.capture.service import CaptureService
from app.config import settings
from app.core.events import EventBus
from app.storage.repository import ImageRepository
from app.storage.thumbnail import ThumbnailCache
from app.stream.coordinator import StreamCoordinator
from app.web.templates import TEMPLATES, WEB_DIR


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus = EventBus()
    registry = CameraRegistry()
    discovery = CameraDiscovery(registry, bus)
    discovery.start(asyncio.get_running_loop())
    coordinator = StreamCoordinator(registry)
    capture_service = CaptureService(registry, coordinator, settings.storage_dir)
    autofocus = SoftwareAutofocus(registry, coordinator)
    image_repository = ImageRepository(settings.storage_dir)
    thumbnail_cache = ThumbnailCache()

    app.state.event_bus = bus
    app.state.camera_registry = registry
    app.state.camera_discovery = discovery
    app.state.stream_coordinator = coordinator
    app.state.capture_service = capture_service
    app.state.autofocus = autofocus
    app.state.image_repository = image_repository
    app.state.thumbnail_cache = thumbnail_cache
    try:
        yield
    finally:
        await coordinator.shutdown()
        discovery.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.mount(
    "/static",
    StaticFiles(directory=str(WEB_DIR / "static")),
    name="static",
)
app.include_router(cameras_router)
app.include_router(controls_router)
app.include_router(events_router)
app.include_router(stream_router)
app.include_router(capture_router)
app.include_router(images_router)
app.include_router(autofocus_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": settings.app_name},
    )


@app.get("/gallery", response_class=HTMLResponse)
async def gallery(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="gallery.html",
        context={"app_name": settings.app_name},
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
