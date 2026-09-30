"""Pure-engine test helpers: build rooms, run commands, fire timers — no I/O."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from app.domain.game import engine
from app.domain.game.core import PHASE_TIMER, Command, Ctx, Event, TimerRequest
from app.domain.game.state import (
    AnswerOption,
    CardData,
    GameContent,
    ModeInfo,
    Player,
    RoomState,
    WordData,
)

MIN_PLAYERS = {"question_list": 3, "impostor": 3, "fill_blank": 3}


def mode(kind: str, **defaults: Any) -> ModeInfo:
    return ModeInfo(
        id=1,
        slug=kind,
        kind=kind,
        title=kind,
        min_players=MIN_PLAYERS.get(kind, 2),
        max_players=12,
        default_settings=defaults,
    )


def cards(card_type: str, n: int = 10, **kw: Any) -> list[CardData]:
    return [
        CardData(id=i, type=card_type, category="friendly", text=f"{card_type} #{i}", **kw)
        for i in range(1, n + 1)
    ]


def content(kind: str, *, card_type: str = "pick_player", n: int = 10, **defaults: Any) -> dict:
    return GameContent(
        mode=mode(kind, **defaults),
        cards=cards(card_type, n),
        words=[WordData(id=i, word=f"word{i}", hint=f"hint{i}") for i in range(1, 6)],
        answers=[AnswerOption(id=i, text=f"answer{i}") for i in range(1, 60)],
    ).model_dump()


@dataclass
class Sim:
    """A room plus a controllable clock; records every event and pending timer."""

    room: RoomState
    now: int = 1_000_000
    seed: int = 7
    events: list[Event] = field(default_factory=list)
    timers: list[TimerRequest] = field(default_factory=list)
    last: Ctx | None = None

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    @property
    def ids(self) -> list[str]:
        return [p.user_id for p in self.room.players]

    @property
    def host(self) -> str:
        return self.room.host_id

    def run(self, type_: str, user: str | None = None, **data: Any) -> Ctx:
        ctx = Ctx(now=self.now, rng=self.rng, grace_ms=30_000)
        engine.apply(self.room, Command(type=type_, user_id=user, data=data), ctx)
        self.events.extend(ctx.events)
        self.timers.extend(ctx.timers)
        self.last = ctx
        return ctx

    def types(self) -> list[str]:
        return [e.type for e in self.events]

    def last_event(self, type_: str) -> Event:
        return next(e for e in reversed(self.events) if e.type == type_)

    def advance(self, ms: int) -> None:
        """Move the clock and fire every due timer, in order."""
        target = self.now + ms
        while True:
            due = sorted((t for t in self.timers if t.due <= target), key=lambda t: t.due)
            if not due:
                break
            t = due[0]
            self.timers.remove(t)
            self.now = max(self.now, t.due)
            self.run("timer", None, kind=t.kind, token=t.token)
        self.now = target

    def fire_phase(self) -> None:
        game = self.room.game
        assert game is not None
        self.run("timer", None, kind=PHASE_TIMER, token=str(game.phase_id))


def make_sim(n_players: int = 4, *, seed: int = 7) -> Sim:
    host = Player(user_id="u1", nickname="P1", joined_at=0)
    room = engine.new_room(code="ABCD", host=host, now=0, max_players=12, mode=None)
    sim = Sim(room=room, seed=seed)
    for i in range(2, n_players + 1):
        sim.run("room.join", f"u{i}", nickname=f"P{i}")
    for uid in sim.ids:
        sim.run("presence.connect", uid)
    return sim


def start(sim: Sim, kind: str, *, settings: dict[str, Any] | None = None, **kw: Any) -> None:
    if settings:
        sim.run("room.update_settings", sim.host, settings=settings)
    sim.run("game.start", sim.host, content=content(kind, **kw))
