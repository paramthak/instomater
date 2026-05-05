from __future__ import annotations

import json
from collections import defaultdict
from typing import DefaultDict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        # session_id -> list of active WebSocket connections
        self._connections: DefaultDict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[session_id].append(ws)

    def disconnect(self, session_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(session_id, [])
        if ws in conns:
            conns.remove(ws)

    async def broadcast(self, session_id: str, message: dict) -> None:
        dead = []
        for ws in list(self._connections.get(session_id, [])):
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(session_id, ws)

    async def send_status(
        self,
        session_id: str,
        message: str,
        stage: str,
        substage_index: int | None = None,
        pill_id: str | None = None,
    ) -> None:
        await self.broadcast(session_id, {
            "type": "status",
            "message": message,
            "stage": stage,
            "substage_index": substage_index,
            "pill_id": pill_id,
        })

    async def send_asset_ready(
        self,
        session_id: str,
        stage: str,
        pill_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        await self.broadcast(session_id, {
            "type": "asset_ready",
            "stage": stage,
            "pill_id": pill_id,
            "data": data,
        })

    async def send_error(
        self,
        session_id: str,
        error_message: str,
        stage: str,
        substage_index: int | None = None,
        pill_id: str | None = None,
    ) -> None:
        await self.broadcast(session_id, {
            "type": "error",
            "message": error_message,
            "stage": stage,
            "substage_index": substage_index,
            "pill_id": pill_id,
        })


# Module-level singleton shared across all request handlers
manager = ConnectionManager()


@router.websocket("/ws/sessions/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    await manager.connect(session_id, websocket)
    try:
        while True:
            # Keep alive — frontend can send pings, we ignore them.
            # We never re-trigger generation from WS messages.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
