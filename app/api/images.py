"""Gallery API: list, thumbnail, full image, delete, zip download."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.storage.bundler import stream_zip


_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/images", tags=["images"])


class NamesBody(BaseModel):
    names: list[str]


@router.get("")
async def list_images(request: Request) -> dict:
    repo = request.app.state.image_repository
    return {
        "images": [
            {"name": m.name, "size": m.size, "mtime": m.mtime}
            for m in repo.list()
        ]
    }


@router.get("/{name}/thumb")
async def get_thumbnail(name: str, request: Request) -> Response:
    repo = request.app.state.image_repository
    cache = request.app.state.thumbnail_cache
    path = repo.path_of(name)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    try:
        data = cache.get(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{name}")
async def get_image(name: str, request: Request) -> FileResponse:
    repo = request.app.state.image_repository
    path = repo.path_of(name)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


@router.delete("")
async def delete_images(body: NamesBody, request: Request) -> dict:
    repo = request.app.state.image_repository
    result = repo.delete(body.names)
    return {"deleted": result}


@router.post("/zip")
async def zip_images(body: NamesBody, request: Request) -> StreamingResponse:
    repo = request.app.state.image_repository
    paths = [p for p in (repo.path_of(n) for n in body.names) if p is not None]
    if not paths:
        raise HTTPException(status_code=400, detail="no valid images selected")
    bundle_name = f"images_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return StreamingResponse(
        stream_zip(paths),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{bundle_name}"'},
    )
