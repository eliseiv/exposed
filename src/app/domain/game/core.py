"""Engine primitives: commands, events, timers, the per-command context, errors.

The engine is PURE: ``apply(room, command, ctx)`` mutates the (freshly deserialized) room and
records side effects in ``ctx`` — events to broadcast, timers to schedule, a game to archive. No
I/O, no clock, no global randomness: ``ctx.now`` and ``ctx.rng`` are injected, which makes every
game rule unit-testable and deterministic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from app.domain.game.state import GameBase, Player, RoomState

PHASE_TIMER = "phase"
GRACE_TIMER = "grace"


class GameError(Exception):
    """A command was rejected. Sent back to the sender only; the state is not saved."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


@dataclass(frozen=True)
class Command:
    type: str
    user_id: str | None = None  # None for system commands (timers)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    to: list[str] | None = None  # None = everyone in the room
    seq: int = 0

    def to_wire(self, server_time: int) -> dict[str, Any]:
        return {"type": self.type, "seq": self.seq, "serverTime": server_time, "data": self.data}


@dataclass(frozen=True)
class TimerRequest:
    kind: str
    due: int  # epoch ms
    token: str


@dataclass
class Ctx:
    now: int
    rng: random.Random
    grace_ms: int = 30_000
    events: list[Event] = field(default_factory=list)
    timers: list[TimerRequest] = field(default_factory=list)
    archive: dict[str, Any] | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def emit(self, type_: str, data: dict[str, Any], to: list[str] | None = None) -> None:
        self.events.append(Event(type=type_, data=data, to=to))

    def schedule(self, kind: str, due: int, token: str) -> None:
        self.timers.append(TimerRequest(kind=kind, due=due, token=token))

    def set_phase(self, game: GameBase, phase: str, seconds: float | None) -> None:
        """Enter ``phase``; with ``seconds`` a phase timer fires ``on_timer`` at ``ends_at``."""
        game.phase = phase
        game.phase_id += 1
        if seconds is None:
            game.ends_at = None
            return
        game.ends_at = self.now + int(seconds * 1000)
        self.schedule(PHASE_TIMER, game.ends_at, str(game.phase_id))


def player_view(room: RoomState, p: Player) -> dict[str, Any]:
    return {
        "userId": p.user_id,
        "nickname": p.nickname,
        "avatarId": p.avatar_id,
        "avatarKey": p.avatar_key,
        "isHost": p.user_id == room.host_id,
        "connected": p.connected,
        "active": p.active,
    }


def brief(room: RoomState, user_id: str | None) -> dict[str, Any] | None:
    """Nickname + avatar of a player — what result screens show."""
    if user_id is None:
        return None
    p = room.player(user_id)
    if p is None:
        return {"userId": user_id, "nickname": None, "avatarId": None, "avatarKey": None}
    return {
        "userId": p.user_id,
        "nickname": p.nickname,
        "avatarId": p.avatar_id,
        "avatarKey": p.avatar_key,
    }


def require_str(data: dict[str, Any], key: str, *, max_len: int = 500) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GameError("invalid_data", f"'{key}' must be a non-empty string")
    value = value.strip()
    if len(value) > max_len:
        raise GameError("invalid_data", f"'{key}' is longer than {max_len}")
    return value
