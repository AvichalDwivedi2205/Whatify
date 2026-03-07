from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class CaptionBus:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[session_id].add(websocket)
            connection_count = len(self._connections[session_id])
        logger.info("caption bus connected session_id=%s connections=%s", session_id, connection_count)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if session_id in self._connections and websocket in self._connections[session_id]:
                self._connections[session_id].remove(websocket)
            connection_count = len(self._connections.get(session_id, set()))
        logger.info("caption bus disconnected session_id=%s connections=%s", session_id, connection_count)

    async def emit(self, session_id: str, text: str) -> None:
        async with self._lock:
            sockets = list(self._connections.get(session_id, set()))
        logger.debug("caption emitted session_id=%s sockets=%s text=%s", session_id, len(sockets), text[:120])
        payload = {"text": text}
        for socket in sockets:
            if socket.application_state == WebSocketState.CONNECTED:
                await socket.send_json(payload)
