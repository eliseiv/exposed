"""``RoomManager`` — the only way anything changes a room.

``execute(code, command)``: lock → load → ``engine.apply`` → number the events → save →
schedule timers → publish, all while holding the room lock (publishing inside the lock keeps
the event order across workers identical to the ``seq`` order). The finished-game archive is
written to PostgreSQL after the lock is released.

Commands that need content from PostgreSQL (``game.start``, choosing a mode) are enriched
BEFORE taking the lock, so the lock is never held across a database round trip.
"""

from __future__ import annotations

import datetime
import logging
import random
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain import metrics
from app.domain.content.repository import ContentRepository
from app.domain.game import engine
from app.domain.game.core import Command, Ctx, Event, GameError, TimerRequest
from app.domain.game.state import ModeInfo, Player, RoomState
from app.domain.models import GameSession
from app.domain.realtime.bus import EventBus
from app.domain.realtime.store import LockTimeoutError, RoomStore
from app.domain.realtime.timers import TimerQueue
from app.observability.logging import log_event

logger = logging.getLogger("app.domain.realtime.manager")

# No 0/O, 1/I/L: codes are read aloud and typed on a phone.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_code(length: int, rng: random.Random | None = None) -> str:
    if rng is not None:
        return "".join(rng.choice(CODE_ALPHABET) for _ in range(length))
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def normalize_code(code: str) -> str:
    return code.strip().upper()


@dataclass
class ExecResult:
    room: RoomState | None  # None when the command closed the room
    ctx: Ctx


SessionFactory = Callable[[], async_sessionmaker[AsyncSession]]
Clock = Callable[[], int]


def _wall_clock() -> int:
    return int(time.time() * 1000)


class RoomManager:
    def __init__(
        self,
        *,
        store: RoomStore,
        bus: EventBus,
        timers: TimerQueue,
        sessions: SessionFactory,
        code_length: int,
        max_players: int,
        grace_ms: int,
        deck_limit: int,
        clock: Clock = _wall_clock,
        rng: random.Random | None = None,
        on_archive: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self.bus = bus
        self.timers = timers
        self._sessions = sessions
        self._code_length = code_length
        self._max_players = max_players
        self._grace_ms = grace_ms
        self._deck_limit = deck_limit
        self.clock = clock
        self._rng = rng or random.SystemRandom()
        self._on_archive = on_archive

    # ---- core ------------------------------------------------------------------------------
    async def execute(self, code: str, cmd: Command) -> ExecResult:
        started = time.perf_counter()
        outcome = "ok"
        try:
            cmd = await self._enrich(code, cmd)
            try:
                async with self.store.lock(code):
                    room = await self.store.load(code)
                    if room is None:
                        raise GameError("room_not_found", "no such room")
                    ctx = Ctx(now=self.clock(), rng=self._rng, grace_ms=self._grace_ms)
                    engine.apply(room, cmd, ctx)
                    self._number(room, ctx.events)
                    if room.closed:
                        ctx.emit("room.closed", {})
                        self._number(room, ctx.events[-1:])
                        await self.store.delete(code)
                    else:
                        await self.store.save(room)
                    await self.timers.schedule(code, ctx.timers)
                    await self.bus.publish(code, ctx.events)
            except LockTimeoutError as exc:
                raise GameError("busy", "the room is busy, retry") from exc
        except GameError as exc:
            outcome = exc.code
            raise
        finally:
            metrics.commands_total.labels(type=cmd.type, outcome=outcome).inc()
            metrics.command_latency_seconds.labels(type=cmd.type).observe(
                time.perf_counter() - started
            )
        if ctx.archive is not None:
            await self._archive(ctx.archive)
        return ExecResult(room=None if room.closed else room, ctx=ctx)

    @staticmethod
    def _number(room: RoomState, events: list[Event]) -> None:
        for ev in events:
            room.seq += 1
            ev.seq = room.seq

    async def _enrich(self, code: str, cmd: Command) -> Command:
        """Attach database content to commands that need it (outside the room lock)."""
        if cmd.type == "room.update_settings" and "modeId" in cmd.data:
            data = {k: v for k, v in cmd.data.items() if k != "modeId"}
            mode_id = cmd.data["modeId"]
            if mode_id is None:
                data["mode"] = None
            else:
                mode = await self._get_mode(mode_id)
                data["mode"] = mode.model_dump()
            return Command(type=cmd.type, user_id=cmd.user_id, data=data)
        if cmd.type == "game.start":
            room = await self.store.load(code)
            if room is None:
                raise GameError("room_not_found", "no such room")
            if cmd.user_id != room.host_id:
                raise GameError("not_host", "only the host can do this")
            async with self._sessions()() as session:
                repo = ContentRepository(session)
                picked: ModeInfo | None
                if room.mode is None:
                    picked = await repo.random_mode(len(room.online_ids()))
                    if picked is None:
                        raise GameError("no_content", "no game fits this number of players")
                else:  # re-read: the catalogue may have changed since the host picked it
                    picked = await repo.get_mode(room.mode.id)
                    if picked is None:
                        raise GameError("mode_unavailable", "this game is no longer available")
                content = await repo.load_content(picked, room.categories, self._deck_limit)
            return Command(
                type=cmd.type, user_id=cmd.user_id, data={"content": content.model_dump()}
            )
        return cmd

    async def _get_mode(self, mode_id: Any) -> ModeInfo:
        if not isinstance(mode_id, int) or isinstance(mode_id, bool):
            raise GameError("invalid_data", "modeId must be an integer")
        async with self._sessions()() as session:
            mode = await ContentRepository(session).get_mode(mode_id)
        if mode is None:
            raise GameError("mode_unavailable", "no such game")
        return mode

    async def _archive(self, record: dict[str, Any]) -> None:
        metrics.games_finished_total.labels(
            kind=record["mode_kind"], reason=record["finish_reason"]
        ).inc()
        try:
            async with self._sessions()() as session:
                session.add(
                    GameSession(
                        room_code=record["room_code"],
                        mode_id=record["mode_id"],
                        mode_kind=record["mode_kind"],
                        host_id=uuid.UUID(record["host_id"]),
                        finish_reason=record["finish_reason"],
                        settings=record["settings"],
                        players=record["players"],
                        result=record["result"],
                        started_at=datetime.datetime.fromtimestamp(
                            record["started_at"] / 1000, tz=datetime.UTC
                        ),
                    )
                )
                await session.commit()
        except Exception as exc:  # the archive must never break the live game
            log_event(logger, logging.ERROR, "game_archive_failed", error=str(exc))
        if self._on_archive is not None:
            await self._on_archive(record)

    # ---- rooms -----------------------------------------------------------------------------
    async def create_room(self, host: Player, mode_id: int | None) -> RoomState:
        mode = await self._get_mode(mode_id) if mode_id is not None else None
        now = self.clock()
        host.joined_at = now
        length = self._code_length
        for attempt in range(30):
            if attempt and attempt % 10 == 0:
                length += 1  # the code space is getting crowded — widen it
            room = engine.new_room(
                code=generate_code(length),
                host=host,
                now=now,
                max_players=self._max_players,
                mode=mode,
            )
            if await self.store.create(room):
                metrics.rooms_created_total.inc()
                return room
        raise GameError("busy", "could not allocate a room code")

    async def join(self, code: str, player: Player) -> RoomState:
        result = await self.execute(
            code,
            Command(
                type="room.join",
                user_id=player.user_id,
                data={
                    "nickname": player.nickname,
                    "avatarId": player.avatar_id,
                    "avatarKey": player.avatar_key,
                },
            ),
        )
        assert result.room is not None
        return result.room

    async def fire_timer(self, code: str, kind: str, token: str) -> None:
        try:
            await self.execute(code, Command(type="timer", data={"kind": kind, "token": token}))
        except GameError as exc:
            if exc.code == "busy":  # lock contention: retry shortly rather than lose the timer
                retry = TimerRequest(kind=kind, due=self.clock() + 200, token=token)
                await self.timers.schedule(code, [retry])
            elif exc.code != "room_not_found":
                log_event(logger, logging.WARNING, "timer_failed", code=code, error=exc.code)
