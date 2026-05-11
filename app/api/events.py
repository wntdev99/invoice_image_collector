"""Server-Sent Events endpoint that proxies the in-process EventBus to clients."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.cameras import serialize_camera
from app.camera.events import CameraAttached, CameraDetached


_log = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

KEEPALIVE_SECONDS = 15.0


def _encode_event(event: Any) -> str | None:
    if isinstance(event, CameraAttached):
        payload = json.dumps(serialize_camera(event.camera))
        return f"event: camera_attached\ndata: {payload}\n\n"
    if isinstance(event, CameraDetached):
        payload = json.dumps({"camera_id": event.camera_id})
        return f"event: camera_detached\ndata: {payload}\n\n"
    return None


@router.get("/events")
async def stream_events(request: Request) -> StreamingResponse:
    bus = request.app.state.event_bus

    async def event_stream() -> AsyncIterator[str]:
        with bus.subscribe() as q:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                payload = _encode_event(event)
                if payload is not None:
                    yield payload

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
