"""Game 2 — wheel of fortune over the players' own content.

1. ``collecting``: every player submits one question, one dare and one gossip.
2. The host spins (``wheel.spin``). The SERVER picks the category (a non-empty wheel segment) and
   the item, removes it from the pool and broadcasts ``wheel.spun`` with the segment index, a
   shared ``spinSeed`` and the timing. Every client animates the wheel deterministically to that
   segment, so all screens show the same spin; the text is shown at ``revealAt``.
3. Spinning an empty wheel ends the game. No points.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.domain.game.core import Command, Ctx, GameError, require_str
from app.domain.game.lifecycle import finish_game
from app.domain.game.modes.base import ModeHandler, ModeSettings
from app.domain.game.state import GameContent, RoomState, WheelGame, WheelItem

SEGMENTS = ("question", "dare", "gossip")


class WheelSettings(ModeSettings):
    collectSec: int | None = Field(default=180, ge=15, le=1800)  # None = until all / host
    spinDurationMs: int = Field(default=4000, ge=1000, le=15000)
    maxTextLen: int = Field(default=200, ge=20, le=500)


class WheelHandler(ModeHandler[WheelGame]):
    kind = "wheel"
    settings_model = WheelSettings
    min_players = 2
    needs_cards = False

    def start(self, room: RoomState, content: GameContent, settings: Any, ctx: Ctx) -> WheelGame:
        game = WheelGame(started_at=ctx.now, settings=settings.model_dump())
        ctx.set_phase(game, "collecting", game.settings["collectSec"])
        ctx.emit(
            "wheel.collecting",
            {"segments": list(SEGMENTS), "phase": game.phase, "endsAt": game.ends_at},
        )
        return game

    def handle(self, room: RoomState, game: WheelGame, cmd: Command, ctx: Ctx) -> None:
        if cmd.type == "wheel.submit":
            self._submit(room, game, cmd, ctx)
        elif cmd.type in ("wheel.spin", "game.next"):
            self.require_host(room, cmd)
            if game.phase == "collecting":
                if not game.submissions:
                    raise GameError("no_content", "nobody has submitted anything yet")
                self._close_collecting(room, game, ctx)
            else:
                self._spin(room, game, ctx)
        else:
            super().handle(room, game, cmd, ctx)

    def on_timer(self, room: RoomState, game: WheelGame, ctx: Ctx) -> None:
        if game.phase == "collecting":
            if game.submissions:
                self._close_collecting(room, game, ctx)
            else:
                finish_game(room, ctx, "no_content")
        elif game.phase == "spinning":
            ctx.set_phase(game, "revealed", None)

    def check_progress(self, room: RoomState, game: WheelGame, ctx: Ctx) -> None:
        if game.phase == "collecting" and self._everyone_submitted(room, game):
            self._close_collecting(room, game, ctx)

    # ---- flow ------------------------------------------------------------------------------
    def _submit(self, room: RoomState, game: WheelGame, cmd: Command, ctx: Ctx) -> None:
        self.require_phase(game, "collecting")
        uid = self.require_active(room, cmd)
        limit = game.settings["maxTextLen"]
        game.submissions[uid] = {seg: require_str(cmd.data, seg, max_len=limit) for seg in SEGMENTS}
        ctx.emit(
            "wheel.progress",
            {"submitted": len(game.submissions), "total": len(room.online_ids())},
        )
        if self._everyone_submitted(room, game):
            self._close_collecting(room, game, ctx)

    @staticmethod
    def _everyone_submitted(room: RoomState, game: WheelGame) -> bool:
        online = room.online_ids()
        return bool(online) and all(uid in game.submissions for uid in online)

    def _close_collecting(self, room: RoomState, game: WheelGame, ctx: Ctx) -> None:
        game.items = [
            WheelItem(category=seg, text=texts[seg], author_id=uid)
            for uid, texts in game.submissions.items()
            for seg in SEGMENTS
        ]
        ctx.set_phase(game, "ready", None)
        ctx.emit("wheel.ready", {"remaining": self._remaining(game), "phase": game.phase})

    def _spin(self, room: RoomState, game: WheelGame, ctx: Ctx) -> None:
        self.require_phase(game, "ready", "revealed")
        if not game.items:
            finish_game(room, ctx, "completed")
            return
        available = [seg for seg in SEGMENTS if any(i.category == seg for i in game.items)]
        category = ctx.rng.choice(available)
        pool = [i for i in game.items if i.category == category]
        item = ctx.rng.choice(pool)
        game.items.remove(item)
        game.round += 1
        duration = game.settings["spinDurationMs"]
        ctx.set_phase(game, "spinning", duration / 1000)
        spin = {
            "round": game.round,
            "category": category,
            "segmentIndex": SEGMENTS.index(category),
            "segments": list(SEGMENTS),
            "text": item.text,
            "spinSeed": ctx.rng.randrange(2**31),
            "spinStartAt": ctx.now,
            "durationMs": duration,
            "revealAt": ctx.now + duration,
            "remaining": self._remaining(game),
            "remainingTotal": len(game.items),
        }
        game.last_spin = spin
        game.reveal_at = spin["revealAt"]
        game.history.append({"round": game.round, "category": category})
        ctx.emit("wheel.spun", {**spin, "phase": game.phase, "endsAt": game.ends_at})

    @staticmethod
    def _remaining(game: WheelGame) -> dict[str, int]:
        return {seg: sum(1 for i in game.items if i.category == seg) for seg in SEGMENTS}

    def project(self, room: RoomState, game: WheelGame, viewer: str) -> dict[str, Any]:
        return {
            **self.common_view(game),
            "segments": list(SEGMENTS),
            "submitted": len(game.submissions),
            "mySubmission": game.submissions.get(viewer),
            "remaining": self._remaining(game),
            "lastSpin": game.last_spin,
        }
