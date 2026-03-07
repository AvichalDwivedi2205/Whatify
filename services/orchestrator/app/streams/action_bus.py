from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.models.contracts import UIAction

logger = logging.getLogger(__name__)


class ActionBus:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._pending: dict[str, dict[str, UIAction]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[session_id].add(websocket)
            pending = list(self._pending.get(session_id, {}).values())
            connection_count = len(self._connections[session_id])
        logger.info(
            "action bus connected session_id=%s connections=%s pending=%s",
            session_id,
            connection_count,
            len(pending),
        )
        for action in pending:
            action.retry_count += 1
            await self._send(websocket, action)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if session_id in self._connections and websocket in self._connections[session_id]:
                self._connections[session_id].remove(websocket)
            connection_count = len(self._connections.get(session_id, set()))
        logger.info("action bus disconnected session_id=%s connections=%s", session_id, connection_count)

    async def emit(self, session_id: str, action: UIAction) -> None:
        async with self._lock:
            self._pending[session_id][action.action_id] = action
            sockets = list(self._connections.get(session_id, set()))
        logger.info(
            "action emitted session_id=%s action_id=%s type=%s sockets=%s",
            session_id,
            action.action_id,
            action.type.value,
            len(sockets),
        )
        for socket in sockets:
            await self._send(socket, action)

    async def ack(self, session_id: str, action_id: str) -> bool:
        async with self._lock:
            if action_id in self._pending.get(session_id, {}):
                del self._pending[session_id][action_id]
                logger.info("action acked session_id=%s action_id=%s", session_id, action_id)
                return True
            return False

    async def pending_count(self, session_id: str) -> int:
        async with self._lock:
            return len(self._pending.get(session_id, {}))

    async def pending_sessions(self) -> list[str]:
        async with self._lock:
            return [session_id for session_id, actions in self._pending.items() if actions]

    async def retry_pending(self, session_id: str) -> None:
        async with self._lock:
            pending = list(self._pending.get(session_id, {}).values())
            sockets = list(self._connections.get(session_id, set()))
        if not sockets:
            return
        logger.debug(
            "retrying pending actions session_id=%s pending=%s sockets=%s",
            session_id,
            len(pending),
            len(sockets),
        )
        for action in pending:
            action.retry_count += 1
            for socket in sockets:
                await self._send(socket, action)

    async def _send(self, websocket: WebSocket, action: UIAction) -> None:
        if websocket.application_state != WebSocketState.CONNECTED:
            return
        await websocket.send_json(action.model_dump(mode="json"))
