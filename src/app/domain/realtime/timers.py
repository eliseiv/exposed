"""Distributed game timers: one Redis sorted set, polled by every worker.

``score`` = due time (epoch ms), ``member`` = ``[code, kind, token]``. A Lua script pops due
members atomically, so each timer fires on exactly one worker. Timers are never cancelled: a
fired timer whose token no longer matches the room state (phase moved on, player reconnected)
is simply ignored by the engine — no cancel/fire race to get wrong.
"""

from __future__ import annotations

import json

import redis.asyncio as redis

from app.domain.game.core import TimerRequest

_POP_DUE = """
local items = redis.call('zrangebyscore', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
if #items > 0 then
  redis.call('zrem', KEYS[1], unpack(items))
end
return items
"""


class TimerQueue:
    def __init__(self, client: redis.Redis, *, prefix: str) -> None:
        self._r = client
        self._key = f"{prefix}:timers"

    async def schedule(self, code: str, timers: list[TimerRequest]) -> None:
        if timers:
            mapping = {json.dumps([code, t.kind, t.token]): t.due for t in timers}
            await self._r.zadd(self._key, mapping)

    async def pop_due(self, now_ms: int, limit: int = 100) -> list[tuple[str, str, str]]:
        raw = await self._r.eval(_POP_DUE, 1, self._key, str(now_ms), str(limit))  # type: ignore[misc]
        out: list[tuple[str, str, str]] = []
        for item in raw or []:
            code, kind, token = json.loads(item)
            out.append((code, kind, token))
        return out

    async def next_due(self) -> int | None:
        first = await self._r.zrange(self._key, 0, 0, withscores=True)
        return int(first[0][1]) if first else None
