"""POST /api/cameras/{id}/capture — shutter endpoint."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.camera.errors import CameraBusy, CameraNotFound
from app.capture.encoder import EncodingFailed
from app.capture.service import CaptureFailed, StreamNotActive
from app.storage.naming import DEFAULT_EXTENSION


_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cameras", tags=["capture"])


class CaptureRequest(BaseModel):
    label: str | None = None
    ext: str | None = DEFAULT_EXTENSION


@router.post("/{camera_id}/capture")
async def capture(camera_id: str, body: CaptureRequest, request: Request) -> dict:
    service = request.app.state.capture_service
    try:
        result = await service.capture(camera_id, body.label, body.ext)
    except CameraNotFound:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    except StreamNotActive as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except CameraBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except CaptureFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except EncodingFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "filename": result.filename,
        "size": result.size,
        "path": result.path,
        "resolution": list(result.resolution),
    }
