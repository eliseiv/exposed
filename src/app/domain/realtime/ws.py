"""The room WebSocket: ``/v1/ws/rooms/{code}``.

Handshake: ``Authorization: Bearer <accessToken>`` (the same JWT as REST). The user must already
be a member of the room (``POST /v1/rooms`` or ``/join``). The first message is always
``room.snapshot`` — the personal view of the room; after it, events stream in ``seq`` order.
A reconnect is just a new connection: a fresh snapshot replaces the client state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket

from app.api_gateway.auth import get_jwt_verifier
from app.domain import metrics
from app.domain.game.core import Command, GameError
from app.domain.game.engine import room_view
from app.domain.realtime.hub import (
    CLOSE_KICKED,
    CLOSE_NOT_FOUND,
    CLOSE_POLICY,
    CLOSE_REPLACED,
    CLOSE_UNAUTHORIZED,
    Connection,
    now_ms,
)
from app.domain.realtime.manager import normalize_code
from app.domain.realtime.runtime import GameRuntime, get_runtime
from app.errors import UnauthorizedError
from app.observability.logging import log_event

logger = logging.getLogger("app.domain.realtime.ws")

router = APIRouter()

# Commands a client may send, each with the data keys it may carry (anything else is dropped).
# System commands (timers, presence, join) are never accepted from a socket.
CLIENT_COMMANDS: dict[str, tuple[str, ...]] = {
    "room.update_settings": ("modeId", "categories", "settings"),
    "room.kick": ("userId",),
    "room.transfer_host": ("userId",),
    "room.leave": (),
    "game.start": (),
    "game.end": (),
    "game.next": (),
    "vote.cast": ("choice",),
    "wheel.submit": ("question", "dare", "gossip"),
    "wheel.spin": (),
    "bomb.pass": (),
    "bomb.return": (),
    "bomb.skip": (),
    "impostor.done_speaking": (),
    "impostor.vote": ("target",),
    "blank.submit": ("optionId", "text"),
    "blank.pick": ("answerId",),
    "hotseat.done": (),
}


class TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self.rate, self.capacity = rate, float(burst)
        self.tokens = float(burst)
        self.at = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


def _bearer(ws: WebSocket) -> str | None:
    header = ws.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return None


def _reply(msg_type: str, msg_id: Any, **data: Any) -> dict[str, Any]:
    return {
        "type": msg_type,
        "seq": None,
        "serverTime": now_ms(),
        "data": {"msgId": msg_id, **data},
    }


@router.websocket("/v1/ws/rooms/{code}")
async def room_socket(ws: WebSocket, code: str) -> None:
    await ws.accept()
    token = _bearer(ws)
    try:
        if token is None:
            raise UnauthorizedError("missing bearer token")
        user = get_jwt_verifier().verify(token)
    except UnauthorizedError:
        await ws.close(code=CLOSE_UNAUTHORIZED, reason="unauthorized")
        return
    await serve(ws, get_runtime(), normalize_code(code), str(user.user_id))


async def serve(ws: WebSocket, runtime: GameRuntime, code: str, uid: str) -> None:
    conn = Connection(ws, code, uid)
    # Attach BEFORE connecting: nothing published from here on can be missed.
    old = runtime.hub.attach(conn)
    if old is not None:
        old.close(CLOSE_REPLACED, "replaced by a newer connection")
    try:
        result = await runtime.manager.execute(code, Command(type="presence.connect", user_id=uid))
    except GameError as exc:
        runtime.hub.detach(conn)
        await ws.send_json(_reply("error", None, code=exc.code, message=exc.message))
        close = CLOSE_KICKED if exc.code == "kicked" else CLOSE_NOT_FOUND
        await ws.close(code=close, reason=exc.code)
        return
    assert result.room is not None
    conn.conn_epoch = int(result.ctx.result["conn_epoch"])
    conn.start(
        {
            "type": "room.snapshot",
            "seq": result.room.seq,
            "serverTime": now_ms(),
            "data": room_view(result.room, uid),
        },
        result.room.seq,
    )
    metrics.ws_connections.inc()
    sender = asyncio.create_task(conn.sender())
    try:
        await _receive_loop(ws, runtime, conn)
    finally:
        metrics.ws_connections.dec()
        runtime.hub.detach(conn)
        conn.close(1000, "bye")
        with contextlib.suppress(Exception):
            await asyncio.wait_for(sender, timeout=2)
        with contextlib.suppress(GameError):
            await runtime.manager.execute(
                code,
                Command(
                    type="presence.disconnect", user_id=uid, data={"conn_epoch": conn.conn_epoch}
                ),
            )


async def _receive_loop(ws: WebSocket, runtime: GameRuntime, conn: Connection) -> None:
    settings = runtime.settings
    bucket = TokenBucket(settings.ws_rate_per_second, settings.ws_rate_burst)
    while not conn.closing:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        raw = message.get("text")
        if raw is None and message.get("bytes") is not None:
            raw = message["bytes"].decode("utf-8", errors="replace")
        if raw is None:
            continue
        if len(raw) > settings.ws_max_message_bytes:
            metrics.ws_messages_total.labels(outcome="too_large").inc()
            conn.close(CLOSE_POLICY, "message too large")
            return
        if not bucket.take():
            metrics.ws_messages_total.labels(outcome="rate_limited").inc()
            conn.send(_reply("error", None, code="rate_limited", message="slow down"))
            continue
        await _handle(runtime, conn, raw)


async def _handle(runtime: GameRuntime, conn: Connection, raw: str) -> None:
    try:
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            raise ValueError
    except ValueError:
        metrics.ws_messages_total.labels(outcome="bad_json").inc()
        conn.send(_reply("error", None, code="bad_message", message="expected a JSON object"))
        return
    msg_type = msg.get("type")
    msg_id = msg.get("msgId")
    data = msg.get("data") or {}
    if not isinstance(data, dict):
        conn.send(_reply("error", msg_id, code="bad_message", message="data must be an object"))
        return

    if msg_type == "ping":
        conn.send(_reply("pong", msg_id))
        return
    if msg_type == "room.resync":
        room = await runtime.store.load(conn.code)
        if room is None:
            conn.send(_reply("error", msg_id, code="room_not_found", message="no such room"))
            return
        conn.resync(
            {
                "type": "room.snapshot",
                "seq": room.seq,
                "serverTime": now_ms(),
                "data": room_view(room, conn.user_id),
            },
            room.seq,
        )
        return

    allowed = CLIENT_COMMANDS.get(msg_type) if isinstance(msg_type, str) else None
    if allowed is None:
        metrics.ws_messages_total.labels(outcome="unknown").inc()
        conn.send(_reply("error", msg_id, code="unknown_command", message=str(msg_type)))
        return
    payload = {k: v for k, v in data.items() if k in allowed}
    try:
        await runtime.manager.execute(
            conn.code, Command(type=str(msg_type), user_id=conn.user_id, data=payload)
        )
    except GameError as exc:
        metrics.ws_messages_total.labels(outcome="rejected").inc()
        conn.send(_reply("error", msg_id, code=exc.code, message=exc.message))
        return
    except Exception:
        log_event(logger, logging.ERROR, "ws_command_failed", type=msg_type)
        logger.exception("ws_command_failed")
        conn.send(_reply("error", msg_id, code="internal_error", message="internal error"))
        return
    metrics.ws_messages_total.labels(outcome="ok").inc()
    conn.send(_reply("ack", msg_id, type=msg_type))
