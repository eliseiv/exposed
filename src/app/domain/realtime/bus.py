"""Cross-worker event fan-out over Redis pub/sub.

Every worker subscribes ONCE to ``{prefix}:events:*`` and hands each message to its local hub,
which delivers it to the sockets of that room connected to this worker. A private event carries
``to`` (user ids) and is filtered by the hub — it is never sent to anyone else's socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis

from app.domain.game.core import Event
from app.observability.logging import log_event

logger = logging.getLogger("app.domain.realtime.bus")

Handler = Callable[[str, list[dict[str, Any]]], Awaitable[None]]


def encode_events(events: list[Event]) -> list[dict[str, Any]]:
    return [{"type": e.type, "seq": e.seq, "data": e.data, "to": e.to} for e in events]


class EventBus:
    def __init__(self, client: redis.Redis, *, prefix: str) -> None:
        self._r = client
        self._prefix = prefix

    def channel(self, code: str) -> str:
        return f"{self._prefix}:events:{code}"

    async def publish(self, code: str, events: list[Event]) -> None:
        if events:
            payload = json.dumps({"code": code, "events": encode_events(events)})
            await self._r.publish(self.channel(code), payload)

    async def run(self, handler: Handler, ready: asyncio.Event | None = None) -> None:
        """Subscribe and dispatch forever; reconnects with backoff on Redis errors."""
        backoff = 0.2
        while True:
            pubsub = self._r.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.psubscribe(f"{self._prefix}:events:*")
                if ready is not None:
                    ready.set()
                backoff = 0.2
                while True:
                    msg = await pubsub.get_message(timeout=1.0)
                    if msg is None or msg.get("type") != "pmessage":
                        continue
                    try:
                        body = json.loads(msg["data"])
                        await handler(body["code"], body["events"])
                    except Exception:  # a bad message must not kill the subscriber
                        logger.exception("bus_dispatch_failed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event(logger, logging.WARNING, "bus_reconnect", error=str(exc))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)
            finally:
                try:
                    await pubsub.aclose()  # type: ignore[no-untyped-call]
                except Exception:
                    logger.debug("pubsub_close_failed", exc_info=True)
