"""REST endpoints for camera listing, details, and enable/disable toggle."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.camera.models import Camera
from app.camera.registry import CameraRegistry


_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


class CameraUpdate(BaseModel):
    enabled: bool | None = None


def serialize_camera(camera: Camera, registry: CameraRegistry | None = None) -> dict:
    out: dict = {
        "id": camera.id,
        "device_path": camera.device_path,
        "name": camera.name,
        "vendor_id": camera.vendor_id,
        "product_id": camera.product_id,
        "serial": camera.serial,
        "bus_path": camera.bus_path,
        "capabilities": {
            "has_autofocus": camera.capabilities.has_autofocus,
            "has_manual_focus": camera.capabilities.has_manual_focus,
            "focus": (
                {
                    "min": camera.capabilities.focus.min,
                    "max": camera.capabilities.focus.max,
                    "step": camera.capabilities.focus.step,
                    "default": camera.capabilities.focus.default,
                }
                if camera.capabilities.focus is not None
                else None
            ),
            "power_line_frequency": (
                {
                    "min": camera.capabilities.power_line_frequency.min,
                    "max": camera.capabilities.power_line_frequency.max,
                    "default": camera.capabilities.power_line_frequency.default,
                    "options": [
                        {"value": v, "label": label}
                        for v, label in camera.capabilities.power_line_frequency.options
                    ],
                }
                if camera.capabilities.power_line_frequency is not None
                else None
            ),
            "formats": list(camera.capabilities.formats),
            "resolutions": [list(r) for r in camera.capabilities.resolutions],
        },
    }
    if registry is not None:
        out["enabled"] = not registry.is_disabled(camera.id)
    return out


@router.get("")
async def list_cameras(request: Request) -> dict:
    registry = request.app.state.camera_registry
    return {"cameras": [serialize_camera(c, registry) for c in registry.list()]}


@router.get("/{camera_id}")
async def get_camera(camera_id: str, request: Request) -> dict:
    registry = request.app.state.camera_registry
    cam = registry.get(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    return serialize_camera(cam, registry)


@router.patch("/{camera_id}")
async def patch_camera(camera_id: str, body: CameraUpdate, request: Request) -> dict:
    registry = request.app.state.camera_registry
    cam = registry.get(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    if body.enabled is not None:
        disabling = not body.enabled
        registry.set_disabled(camera_id, disabling)
        if disabling:
            # Vacate V4L2 handle so other apps can claim it.
            coord = request.app.state.stream_coordinator
            await coord.force_close_active_if(camera_id)
    return serialize_camera(cam, registry)
