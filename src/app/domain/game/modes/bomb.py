"""Game 3 — the bomb.

A random player gets the bomb and a random question; one fuse runs for the whole round (its
length is random within the settings and, by default, hidden). The holder answers out loud and
passes the bomb (``bomb.pass``) to a random other player, who gets a new question.

* ``bomb.return`` — the NEW holder may send the bomb straight back once, within
  ``returnWindowSec`` (the previous player cheated / skipped the answer).
* ``bomb.skip`` — take another question; with probability ``skipExplodeChance`` the bomb
  explodes immediately.

When the fuse runs out, the holder is the loser of the round (+1 explosion). The leaderboard
ranks players by explosions (fewer is better).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from app.domain.game.core import Command, Ctx, GameError, brief
from app.domain.game.lifecycle import finish_game
from app.domain.game.modes.base import ModeHandler, ModeSettings
from app.domain.game.state import BombGame, GameContent, RoomState


class BombSettings(ModeSettings):
    fuseMinSec: int = Field(default=40, ge=5, le=600)
    fuseMaxSec: int = Field(default=90, ge=5, le=600)
    showTimer: bool = False
    returnWindowSec: int = Field(default=5, ge=0, le=60)  # 0 disables `bomb.return`
    skipExplodeChance: float = Field(default=0.25, ge=0.0, le=1.0)
    roundsCount: int = Field(default=3, ge=1, le=50)

    @model_validator(mode="after")
    def _fuse_range(self) -> BombSettings:
        if self.fuseMinSec > self.fuseMaxSec:
            raise ValueError("fuseMinSec must be <= fuseMaxSec")
        return self


class BombHandler(ModeHandler[BombGame]):
    kind = "bomb"
    settings_model = BombSettings
    min_players = 2
    scores_ascending = True  # explosions

    def validate_start(self, room: RoomState, settings: Any, content: GameContent) -> None:
        if not content.cards:
            raise GameError("no_content", "this game has no questions in the selected categories")

    def start(self, room: RoomState, content: GameContent, settings: Any, ctx: Ctx) -> BombGame:
        deck = list(content.cards)
        ctx.rng.shuffle(deck)
        game = BombGame(
            started_at=ctx.now,
            settings=settings.model_dump(),
            deck=deck,
            scores=dict.fromkeys(room.active_ids(), 0),
        )
        self._start_round(room, game, ctx)
        return game

    # ---- commands --------------------------------------------------------------------------
    def handle(self, room: RoomState, game: BombGame, cmd: Command, ctx: Ctx) -> None:
        if cmd.type == "bomb.pass":
            self._require_holder(game, cmd)
            self._pass(room, game, ctx, from_user=game.holder, reason="answered")
        elif cmd.type == "bomb.return":
            self._return(room, game, cmd, ctx)
        elif cmd.type == "bomb.skip":
            self._require_holder(game, cmd)
            if ctx.rng.random() < game.settings["skipExplodeChance"]:
                self._explode(room, game, ctx, reason="skip")
            else:
                game.question = self._draw_question(game)
                ctx.emit(
                    "bomb.question_changed",
                    {"holder": brief(room, game.holder), "question": game.question},
                )
        elif cmd.type == "game.next":
            self.require_host(room, cmd)
            self.require_phase(game, "exploded")
            if game.round >= game.settings["roundsCount"]:
                finish_game(room, ctx, "completed")
            else:
                self._start_round(room, game, ctx)
        else:
            super().handle(room, game, cmd, ctx)

    def on_timer(self, room: RoomState, game: BombGame, ctx: Ctx) -> None:
        if game.phase == "ticking":
            self._explode(room, game, ctx, reason="fuse")

    def on_player_inactive(self, room: RoomState, game: BombGame, user_id: str, ctx: Ctx) -> None:
        if game.prev_holder == user_id:
            game.prev_holder = None
        if game.phase == "ticking" and game.holder == user_id:
            self._pass(room, game, ctx, from_user=user_id, reason="left")

    # ---- flow ------------------------------------------------------------------------------
    def _require_holder(self, game: BombGame, cmd: Command) -> None:
        self.require_phase(game, "ticking")
        if cmd.user_id != game.holder:
            raise GameError("not_your_turn", "you are not holding the bomb")

    def _draw_question(self, game: BombGame) -> str:
        card = game.deck[game.deck_pos % len(game.deck)]
        game.deck_pos += 1
        return card.text

    def _start_round(self, room: RoomState, game: BombGame, ctx: Ctx) -> None:
        game.round += 1
        s = game.settings
        fuse = ctx.rng.uniform(s["fuseMinSec"], s["fuseMaxSec"])
        game.holder = ctx.rng.choice(room.active_ids())
        game.prev_holder = None
        game.return_until = None
        game.returned = False
        game.question = self._draw_question(game)
        ctx.set_phase(game, "ticking", fuse)
        game.fuse_ends_at = game.ends_at
        ctx.emit(
            "bomb.round_started",
            {
                "round": game.round,
                "roundsCount": s["roundsCount"],
                "holder": brief(room, game.holder),
                "question": game.question,
                "showTimer": s["showTimer"],
                "endsAt": self._visible_end(game),
                "phase": game.phase,
            },
        )

    def _pass(
        self, room: RoomState, game: BombGame, ctx: Ctx, *, from_user: str | None, reason: str
    ) -> None:
        others = [u for u in room.active_ids() if u != from_user]
        if not others:
            return
        to_user = ctx.rng.choice(others)
        game.prev_holder = from_user if reason == "answered" else None
        game.holder = to_user
        game.question = self._draw_question(game)
        window = game.settings["returnWindowSec"]
        game.returned = False
        game.return_until = ctx.now + window * 1000 if window and game.prev_holder else None
        ctx.emit(
            "bomb.passed",
            {
                "from": brief(room, from_user),
                "to": brief(room, to_user),
                "question": game.question,
                "returnUntil": game.return_until,
                "reason": reason,
            },
        )

    def _return(self, room: RoomState, game: BombGame, cmd: Command, ctx: Ctx) -> None:
        self._require_holder(game, cmd)
        if (
            game.prev_holder is None
            or game.returned
            or game.return_until is None
            or ctx.now > game.return_until
        ):
            raise GameError("return_not_allowed", "the bomb can no longer be returned")
        prev = game.prev_holder
        game.holder, game.prev_holder = prev, None
        game.returned = True
        game.return_until = None
        game.question = self._draw_question(game)
        ctx.emit(
            "bomb.returned",
            {
                "from": brief(room, cmd.user_id),
                "to": brief(room, prev),
                "question": game.question,
            },
        )

    def _explode(self, room: RoomState, game: BombGame, ctx: Ctx, *, reason: str) -> None:
        loser = game.holder
        if loser is not None:
            game.scores[loser] = game.scores.get(loser, 0) + 1
        game.last_loser = loser
        game.history.append({"round": game.round, "loser": loser, "reason": reason})
        ctx.set_phase(game, "exploded", None)
        ctx.emit(
            "bomb.exploded",
            {
                "round": game.round,
                "loser": brief(room, loser),
                "reason": reason,
                "scores": dict(game.scores),
                "isLastRound": game.round >= game.settings["roundsCount"],
                "phase": game.phase,
            },
        )

    @staticmethod
    def _visible_end(game: BombGame) -> int | None:
        return game.fuse_ends_at if game.settings["showTimer"] else None

    def project(self, room: RoomState, game: BombGame, viewer: str) -> dict[str, Any]:
        view = self.common_view(game)
        view["endsAt"] = self._visible_end(game) if game.phase == "ticking" else None
        view.update(
            {
                "holder": brief(room, game.holder),
                "question": game.question,
                "returnUntil": game.return_until if viewer == game.holder else None,
                "canReturn": viewer == game.holder
                and game.prev_holder is not None
                and not game.returned
                and game.return_until is not None,
                "lastLoser": brief(room, game.last_loser),
            }
        )
        return view
