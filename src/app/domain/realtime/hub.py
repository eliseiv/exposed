"""Sockets connected to THIS worker, grouped by room.

Delivery is decoupled from the bus through a bounded per-connection queue and a sender task, so
one slow client can never stall the subscriber (it is disconnected instead and resyncs on
reconnect through a fresh snapshot).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger("app.domain.realtime.hub")

# WebSocket close codes of the protocol (docs/realtime-protocol.md).
CLOSE_LEFT = 4000
CLOSE_REPLACED = 4001
CLOSE_KICKED = 4003
CLOSE_NOT_FOUND = 4004
CLOSE_UNAUTHORIZED = 4401
CLOSE_POLICY = 1008
CLOSE_SERVER_RESTART = 1012
CLOSE_OVERLOADED = 1013

_QUEUE_MAX = 512


@dataclass(frozen=True)
class _Close:
    code: int
    reason: str


def now_ms() -> int:
    return int(time.time() * 1000)


class Connection:
    def __init__(self, ws: WebSocket, code: str, user_id: str) -> None:
        self.ws = ws
        self.code = code
        self.user_id = user_id
        self.conn_epoch = 0
        # Events with seq <= this were already covered by the snapshot the client received.
        self.min_seq = 0
        self._queue: asyncio.Queue[dict[str, Any] | _Close] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._closing = False
        # Until the snapshot is queued, events are parked here: the snapshot must go FIRST.
        self._pending: list[dict[str, Any]] | None = []

    def start(self, snapshot: dict[str, Any], seq: int) -> None:
        """Queue the snapshot, then every parked event newer than it."""
        self.min_seq = seq
        self.send(snapshot)
        pending, self._pending = self._pending or [], None
        for message in pending:
            if message["seq"] > seq:
                self.send(message)

    def resync(self, snapshot: dict[str, Any], seq: int) -> None:
        self.min_seq = max(self.min_seq, seq)
        self.send(snapshot)

    def send(self, message: dict[str, Any]) -> None:
        if self._closing:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self.close(CLOSE_OVERLOADED, "client too slow")

    def deliver(self, event: dict[str, Any]) -> None:
        message = {
            "type": event["type"],
            "seq": event["seq"],
            "serverTime": now_ms(),
            "data": event["data"],
        }
        if self._pending is not None:
            self._pending.append(message)
        else:
            self.send(message)

    def close(self, code: int, reason: str) -> None:
        if self._closing:
            return
        self._closing = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_Close(code, reason))

    @property
    def closing(self) -> bool:
        return self._closing

    async def sender(self) -> None:
        """Drain the queue into the socket until a close marker or a send failure."""
        while True:
            item = await self._queue.get()
            if isinstance(item, _Close):
                if self.ws.application_state == WebSocketState.CONNECTED:
                    with contextlib.suppress(Exception):
                        await self.ws.close(code=item.code, reason=item.reason)
                return
            seq = item.get("seq")
            stale = isinstance(seq, int) and 0 < seq <= self.min_seq
            if stale and item["type"] != "room.snapshot":
                continue  # already reflected in the snapshot the client got
            try:
                await self.ws.send_json(item)
            except Exception:
                return


class Hub:
    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, Connection]] = {}

    def attach(self, conn: Connection) -> Connection | None:
        """Register ``conn``; returns the connection it replaced (same user, same room)."""
        room = self._rooms.setdefault(conn.code, {})
        old = room.get(conn.user_id)
        room[conn.user_id] = conn
        return old

    def detach(self, conn: Connection) -> None:
        room = self._rooms.get(conn.code)
        if room is not None and room.get(conn.user_id) is conn:
            del room[conn.user_id]
            if not room:
                del self._rooms[conn.code]

    def connections(self) -> list[Connection]:
        return [c for room in self._rooms.values() for c in room.values()]

    def count(self) -> int:
        return sum(len(r) for r in self._rooms.values())

    async def dispatch(self, code: str, events: list[dict[str, Any]]) -> None:
        room = self._rooms.get(code)
        if not room:
            return
        for conn in list(room.values()):
            for ev in events:
                to = ev.get("to")
                if to is not None and conn.user_id not in to:
                    continue
                conn.deliver(ev)
                if ev["type"] == "player.left" and ev["data"].get("userId") == conn.user_id:
                    kicked = ev["data"].get("reason") == "kicked"
                    conn.close(CLOSE_KICKED if kicked else CLOSE_LEFT, ev["data"]["reason"])
                elif ev["type"] == "room.closed":
                    conn.close(CLOSE_LEFT, "room closed")
