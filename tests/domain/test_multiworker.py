"""Two workers on one Redis: events fan out to both; a timer fires exactly once; the room lock
serializes concurrent commands."""

from __future__ import annotations

import asyncio
from typing import Any

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.config import get_domain_settings
from app.domain.game.core import Command, TimerRequest
from app.domain.game.state import Player
from app.domain.realtime.runtime import GameRuntime
from app.domain.realtime.timers import TimerQueue


class StubConn:
    def __init__(self, code: str, user_id: str) -> None:
        self.code, self.user_id = code, user_id
        self.got: list[dict[str, Any]] = []
        self.closed: int | None = None

    def deliver(self, event: dict[str, Any]) -> None:
        self.got.append(event)

    def close(self, code: int, reason: str) -> None:
        self.closed = code


async def _worker(client: redis.Redis, sm: async_sessionmaker[AsyncSession]) -> GameRuntime:
    rt = GameRuntime(client, get_domain_settings(), sessions=lambda: sm)
    rt.subscribed = asyncio.Event()
    rt._tasks = [asyncio.create_task(rt.bus.run(rt.hub.dispatch, rt.subscribed))]
    await asyncio.wait_for(rt.subscribed.wait(), timeout=10)
    return rt


async def test_events_reach_sockets_on_every_worker(
    redis_client: redis.Redis, sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    a = await _worker(redis_client, sessionmaker_)
    b = await _worker(redis_client, sessionmaker_)
    try:
        room = await a.manager.create_room(Player(user_id="h", nickname="H", joined_at=0), None)
        on_a, on_b = StubConn(room.code, "h"), StubConn(room.code, "g")
        a.hub.attach(on_a)  # type: ignore[arg-type]
        b.hub.attach(on_b)  # type: ignore[arg-type]
        await b.manager.join(room.code, Player(user_id="g", nickname="G", joined_at=0))
        for _ in range(100):
            if on_a.got and on_b.got:
                break
            await asyncio.sleep(0.02)
        assert on_a.got[0]["type"] == on_b.got[0]["type"] == "player.joined"
        # a kick delivered on worker A closes the socket that lives on worker B
        await a.manager.execute(room.code, Command("room.kick", "h", {"userId": "g"}))
        for _ in range(100):
            if on_b.closed:
                break
            await asyncio.sleep(0.02)
        assert on_b.closed == 4003
    finally:
        await a.stop()
        await b.stop()


async def test_timer_fires_exactly_once_across_workers(redis_client: redis.Redis) -> None:
    queues = [TimerQueue(redis_client, prefix="t") for _ in range(4)]
    await queues[0].schedule("ROOM", [TimerRequest(kind="phase", due=1000, token="1")])
    popped = await asyncio.gather(*(q.pop_due(2000) for q in queues))
    assert sum(len(p) for p in popped) == 1
    assert await queues[0].next_due() is None


async def test_concurrent_commands_are_serialized(
    redis_client: redis.Redis, sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    rt = await _worker(redis_client, sessionmaker_)
    try:
        room = await rt.manager.create_room(Player(user_id="h", nickname="H", joined_at=0), None)
        players = [Player(user_id=f"p{i}", nickname=f"P{i}", joined_at=0) for i in range(8)]
        await asyncio.gather(*(rt.manager.join(room.code, p) for p in players))
        stored = await rt.store.load(room.code)
        assert stored is not None
        assert len(stored.players) == 9  # no lost update
        assert stored.seq == 8  # one player.joined each, numbered without gaps
    finally:
        await rt.stop()
