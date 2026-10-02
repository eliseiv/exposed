"""Per-worker realtime runtime: the bus subscriber, the timer poller and the local hub.

``start()``/``stop()`` are wired as ``DomainRegistry.on_startup``/``on_shutdown``. Tests build
their own runtime on a Redis container and install it with ``set_runtime()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import redis.asyncio as redis

from app.api_gateway.rate_limit import get_redis
from app.db import get_sessionmaker
from app.domain.config import DomainSettings, get_domain_settings
from app.domain.realtime.bus import EventBus
from app.domain.realtime.hub import CLOSE_SERVER_RESTART, Hub
from app.domain.realtime.manager import Clock, RoomManager, SessionFactory, _wall_clock
from app.domain.realtime.store import RoomStore
from app.domain.realtime.timers import TimerQueue

logger = logging.getLogger("app.domain.realtime.runtime")


class GameRuntime:
    def __init__(
        self,
        client: redis.Redis,
        settings: DomainSettings,
        *,
        sessions: SessionFactory = get_sessionmaker,
        clock: Clock = _wall_clock,
    ) -> None:
        prefix = settings.redis_key_prefix
        self.settings = settings
        self.redis = client
        self.hub = Hub()
        self.store = RoomStore(
            client,
            prefix=prefix,
            ttl_seconds=settings.room_ttl_seconds,
            lock_timeout_ms=settings.room_lock_timeout_ms,
        )
        self.bus = EventBus(client, prefix=prefix)
        self.timers = TimerQueue(client, prefix=prefix)
        self.manager = RoomManager(
            store=self.store,
            bus=self.bus,
            timers=self.timers,
            sessions=sessions,
            code_length=settings.room_code_length,
            max_players=settings.room_max_players,
            grace_ms=settings.reconnect_grace_seconds * 1000,
            deck_limit=settings.game_deck_limit,
            locales=settings.locales(),
            clock=clock,
        )
        self._tasks: list[asyncio.Task[Any]] = []
        self.subscribed = asyncio.Event()

    async def start(self) -> None:
        if self._tasks:
            return
        self.subscribed = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self.bus.run(self.hub.dispatch, self.subscribed), name="game-bus"),
            asyncio.create_task(self._poll_timers(), name="game-timers"),
        ]
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.subscribed.wait(), timeout=5)

    async def stop(self) -> None:
        # Tell clients to reconnect (to another worker) instead of waiting for a dead socket.
        for conn in self.hub.connections():
            conn.close(CLOSE_SERVER_RESTART, "server restart")
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []

    async def fire_due_timers(self) -> int:
        """Pop and apply every due timer once. Returns how many fired (tests call this)."""
        due = await self.timers.pop_due(self.manager.clock())
        for code, kind, token in due:
            await self.manager.fire_timer(code, kind, token)
        return len(due)

    async def _poll_timers(self) -> None:
        interval = self.settings.timer_poll_interval_ms / 1000
        while True:
            try:
                await self.fire_due_timers()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("timer_poll_failed")
            await asyncio.sleep(interval)


_runtime: GameRuntime | None = None


def get_runtime() -> GameRuntime:
    global _runtime
    if _runtime is None:
        _runtime = GameRuntime(get_redis(), get_domain_settings())
    return _runtime


def set_runtime(runtime: GameRuntime | None) -> None:
    global _runtime
    _runtime = runtime


async def start_runtime() -> None:
    await get_runtime().start()


async def stop_runtime() -> None:
    if _runtime is not None:
        await _runtime.stop()
    set_runtime(None)
