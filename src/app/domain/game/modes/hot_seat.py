"""Game 6 — "21 questions": a random player answers a random question, 21 rounds, no points.

Players are drawn from a shuffled "bag" so everybody gets a turn before anyone repeats (and never
the same player twice in a row across a refill). The player (or the host) presses "done".
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.domain.game.core import Command, Ctx, GameError, brief
from app.domain.game.lifecycle import finish_game
from app.domain.game.modes.base import ModeHandler, ModeSettings
from app.domain.game.state import GameContent, HotSeatGame, RoomState


class HotSeatSettings(ModeSettings):
    roundsCount: int = Field(default=21, ge=1, le=200)
    # Optional per-turn timer; None = no timer, the player presses "done".
    turnSec: int | None = Field(default=None, ge=5, le=600)


class HotSeatHandler(ModeHandler[HotSeatGame]):
    kind = "hot_seat"
    settings_model = HotSeatSettings
    min_players = 2

    def validate_start(self, room: RoomState, settings: Any, content: GameContent) -> None:
        if not content.cards:
            raise GameError("no_content", "this game has no questions in the selected categories")

    def start(self, room: RoomState, content: GameContent, settings: Any, ctx: Ctx) -> HotSeatGame:
        deck = list(content.cards)
        ctx.rng.shuffle(deck)
        game = HotSeatGame(started_at=ctx.now, settings=settings.model_dump(), deck=deck)
        self._next_turn(room, game, ctx)
        return game

    def handle(self, room: RoomState, game: HotSeatGame, cmd: Command, ctx: Ctx) -> None:
        if cmd.type in ("hotseat.done", "game.next"):
            self.require_phase(game, "turn")
            if cmd.user_id not in (game.player, room.host_id):
                raise GameError("not_your_turn", "only the answering player or the host")
            self._next_turn(room, game, ctx)
        else:
            super().handle(room, game, cmd, ctx)

    def on_timer(self, room: RoomState, game: HotSeatGame, ctx: Ctx) -> None:
        if game.phase == "turn":
            self._next_turn(room, game, ctx)

    def on_player_inactive(
        self, room: RoomState, game: HotSeatGame, user_id: str, ctx: Ctx
    ) -> None:
        game.bag = [u for u in game.bag if u != user_id]
        if game.player == user_id and game.phase == "turn":
            game.round -= 1  # the turn did not happen — do not burn a round on it
            self._next_turn(room, game, ctx)

    def _draw_player(self, room: RoomState, game: HotSeatGame, ctx: Ctx) -> str:
        active = room.active_ids()
        game.bag = [u for u in game.bag if u in active]
        if not game.bag:
            bag = list(active)
            ctx.rng.shuffle(bag)
            if len(bag) > 1 and bag[0] == game.player:
                bag.append(bag.pop(0))
            game.bag = bag
        return game.bag.pop(0)

    def _next_turn(self, room: RoomState, game: HotSeatGame, ctx: Ctx) -> None:
        if game.round >= game.settings["roundsCount"]:
            finish_game(room, ctx, "completed")
            return
        game.round += 1
        game.player = self._draw_player(room, game, ctx)
        card = game.deck[game.deck_pos % len(game.deck)]
        game.deck_pos += 1
        game.question = card.text
        game.history.append({"round": game.round, "userId": game.player, "cardId": card.id})
        ctx.set_phase(game, "turn", game.settings["turnSec"])
        ctx.emit("hotseat.turn", self._turn_view(room, game))

    def _turn_view(self, room: RoomState, game: HotSeatGame) -> dict[str, Any]:
        return {
            "round": game.round,
            "roundsCount": game.settings["roundsCount"],
            "player": brief(room, game.player),
            "question": game.question,
            "phase": game.phase,
            "endsAt": game.ends_at,
        }

    def project(self, room: RoomState, game: HotSeatGame, viewer: str) -> dict[str, Any]:
        return {**self.common_view(game), "turn": self._turn_view(room, game)}
