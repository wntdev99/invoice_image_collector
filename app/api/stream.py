"""Stream endpoints: /cam/{id} (HTML page) and /stream/{id} (MJPEG)."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.api.cameras import serialize_camera
from app.config import settings
from app.camera.errors import CameraBusy, CameraDisabled, CameraNotFound
from app.stream.mjpeg import MJPEGStreamProvider
from app.web.templates import TEMPLATES


_log = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])

_mjpeg = MJPEGStreamProvider(quality=85)


class StreamConfigUpdate(BaseModel):
    width: int
    height: int


@router.get("/cam/{camera_id}", response_class=HTMLResponse)
async def camera_page(camera_id: str, request: Request) -> HTMLResponse:
    registry = request.app.state.camera_registry
    cam = registry.get(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    return TEMPLATES.TemplateResponse(
        request=request,
        name="camera.html",
        context={"app_name": settings.app_name, "camera": serialize_camera(cam)},
    )


@router.get("/stream/{camera_id}")
async def mjpeg_stream(camera_id: str, request: Request) -> StreamingResponse:
    coord = request.app.state.stream_coordinator
    loop = asyncio.get_running_loop()

    # Acquire BEFORE returning the response, so we can return proper HTTP errors
    # (404 / 503) instead of an aborted multipart stream.
    try:
        source = await coord.acquire(camera_id, loop)
    except CameraNotFound:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    except CameraDisabled:
        raise HTTPException(
            status_code=409,
            detail=f"camera is disabled (toggle it back on from the main page): {camera_id}",
        )
    except CameraBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    async def stream_generator():
        try:
            async for payload in _mjpeg.iter_payloads(source):
                yield payload
        finally:
            await coord.release(camera_id, source)

    return StreamingResponse(
        stream_generator(),
        media_type=_mjpeg.media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/cameras/{camera_id}/stream-config")
async def get_stream_config(camera_id: str, request: Request) -> dict:
    registry = request.app.state.camera_registry
    camera = registry.get(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    coord = request.app.state.stream_coordinator
    preferred = coord.get_preferred_resolution(camera_id)
    return {
        "preferred": list(preferred) if preferred else None,
        "supported": [list(r) for r in camera.capabilities.resolutions],
    }


@router.put("/api/cameras/{camera_id}/stream-config")
async def put_stream_config(
    camera_id: str, body: StreamConfigUpdate, request: Request
) -> dict:
    registry = request.app.state.camera_registry
    camera = registry.get(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    requested = (body.width, body.height)
    if camera.capabilities.resolutions and requested not in camera.capabilities.resolutions:
        raise HTTPException(
            status_code=400,
            detail=f"resolution {body.width}x{body.height} not in camera's supported list",
        )
    coord = request.app.state.stream_coordinator
    await coord.change_resolution(camera_id, body.width, body.height)
    return {"preferred": [body.width, body.height]}
