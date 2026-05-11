"""REST endpoints for camera listing and details."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.camera.models import Camera


router = APIRouter(prefix="/api/cameras", tags=["cameras"])


def serialize_camera(camera: Camera) -> dict:
    return {
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
            "formats": list(camera.capabilities.formats),
            "resolutions": [list(r) for r in camera.capabilities.resolutions],
        },
    }


@router.get("")
async def list_cameras(request: Request) -> dict:
    registry = request.app.state.camera_registry
    return {"cameras": [serialize_camera(c) for c in registry.list()]}


@router.get("/{camera_id}")
async def get_camera(camera_id: str, request: Request) -> dict:
    registry = request.app.state.camera_registry
    cam = registry.get(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    return serialize_camera(cam)
