"""Room state in Redis + the per-room lock.

One JSON document per room (``{prefix}:room:{code}``) with a sliding TTL. Every mutation runs
under a short distributed lock (``SET NX PX`` + compare-and-delete release), so commands for one
room are applied strictly one at a time across every worker and replica.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis

from app.domain.game.state import RoomState

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class LockTimeoutError(Exception):
    pass


class RoomStore:
    def __init__(
        self,
        client: redis.Redis,
        *,
        prefix: str,
        ttl_seconds: int,
        lock_timeout_ms: int,
    ) -> None:
        self._r = client
        self._prefix = prefix
        self._ttl = ttl_seconds
        self._lock_timeout_ms = lock_timeout_ms

    def _key(self, code: str) -> str:
        return f"{self._prefix}:room:{code}"

    async def create(self, room: RoomState) -> bool:
        """Store a NEW room; False when the code is taken (the SET NX doubles as reservation)."""
        ok = await self._r.set(self._key(room.code), room.model_dump_json(), nx=True, ex=self._ttl)
        return bool(ok)

    async def load(self, code: str) -> RoomState | None:
        raw = await self._r.get(self._key(code))
        return RoomState.model_validate_json(raw) if raw else None

    async def save(self, room: RoomState) -> None:
        await self._r.set(self._key(room.code), room.model_dump_json(), ex=self._ttl)

    async def delete(self, code: str) -> None:
        await self._r.delete(self._key(code))

    @asynccontextmanager
    async def lock(self, code: str) -> AsyncIterator[None]:
        key = f"{self._prefix}:lock:{code}"
        token = secrets.token_hex(8)
        deadline = time.monotonic() + self._lock_timeout_ms / 1000
        delay = 0.005
        while not await self._r.set(key, token, nx=True, px=self._lock_timeout_ms):
            if time.monotonic() >= deadline:
                raise LockTimeoutError(code)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.05)
        try:
            yield
        finally:
            await self._r.eval(_RELEASE, 1, key, token)  # type: ignore[misc]
