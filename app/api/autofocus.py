"""POST /api/cameras/{id}/autofocus — trigger software autofocus sweep."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.camera.errors import CameraNotFound
from app.capture.autofocus import AutofocusError


_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cameras", tags=["autofocus"])


@router.post("/{camera_id}/autofocus")
async def trigger_autofocus(camera_id: str, request: Request) -> dict:
    af = request.app.state.autofocus
    try:
        result = await af.run(camera_id)
    except CameraNotFound:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    except AutofocusError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "focus": result.best_focus,
        "sharpness": result.best_sharpness,
        "elapsed_ms": result.elapsed_ms,
        "attempts": result.attempts,
    }
