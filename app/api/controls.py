"""REST endpoints for live camera controls (focus, autofocus, zoom).

Controls are only addressable while the camera is the *active* streaming
camera — otherwise we'd need to open/close the V4L2 device behind the
user's back, which would race with stream lifecycle. If no active stream,
return 409.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cameras", tags=["controls"])


class ControlsUpdate(BaseModel):
    focus: int | None = None
    autofocus: bool | None = None
    zoom: int | None = None             # absolute mode 슬라이더
    zoom_step: str | None = None        # relative mode: "in"/"out"/"stop"
    zoom_step_ms: int | None = None     # relative mode motor 명령 duration (선택)
    power_line_frequency: int | None = None


def _active_source_or_409(request: Request, camera_id: str):
    registry = request.app.state.camera_registry
    if registry.get(camera_id) is None:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    coord = request.app.state.stream_coordinator
    source = coord.get_active_source_for(camera_id)
    if source is None:
        raise HTTPException(
            status_code=409,
            detail="camera is not currently streaming; open /cam/{id} first",
        )
    return source


@router.get("/{camera_id}/controls")
async def get_controls(camera_id: str, request: Request) -> dict:
    source = _active_source_or_409(request, camera_id)
    return source.controller.snapshot()


@router.patch("/{camera_id}/controls")
async def patch_controls(
    camera_id: str, body: ControlsUpdate, request: Request
) -> dict:
    source = _active_source_or_409(request, camera_id)
    applied: dict = {}
    if body.focus is not None:
        actual = source.controller.set_focus(body.focus)
        applied["focus"] = actual
    if body.autofocus is not None:
        actual_af = source.controller.set_autofocus(body.autofocus)
        applied["autofocus"] = actual_af
    if body.zoom is not None:
        applied["zoom"] = source.controller.set_zoom(body.zoom)
    if body.zoom_step is not None:
        applied["zoom_step"] = source.controller.zoom_step(
            body.zoom_step, duration_ms=body.zoom_step_ms
        )
    if body.power_line_frequency is not None:
        applied["power_line_frequency"] = source.controller.set_power_line_frequency(
            body.power_line_frequency
        )
    return applied
