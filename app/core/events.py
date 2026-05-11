"""In-process publish/subscribe bus.

Generic infrastructure — domain event types live alongside their domains
(e.g. app.camera.events). The bus itself does not know about cameras.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any


_log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, queue_size: int = 64) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[Any]] = set()

    def publish(self, event: Any) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                _log.warning("subscriber queue full, dropping event %r", type(event).__name__)

    async def subscribe(self) -> AsyncIterator[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)
