"""Game 5 — fill in the blank.

Every round one player is the judge (rotating). Everybody sees a phrase with a gap (``___``).
Non-judges fill it:

* ``answerMode=options`` — each player privately gets their OWN set of ``optionsPerPlayer``
  options from the answer pool (no two players share an option within a round);
* ``answerMode=free`` — each player types their own ending.

When all answered (or the timer ran out) the judge sees the answers shuffled and anonymous and
picks the best (timeout → random). The author gets a point and the authors are revealed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.domain.game.core import Command, Ctx, GameError, brief, require_str
from app.domain.game.lifecycle import finish_game
from app.domain.game.modes.base import ModeHandler, ModeSettings
from app.domain.game.state import (
    AnswerOption,
    BlankSubmission,
    FillBlankGame,
    GameContent,
    RoomState,
)

BLANK = "___"


class FillBlankSettings(ModeSettings):
    answerMode: Literal["options", "free"] = "options"
    roundsCount: int = Field(default=5, ge=1, le=50)
    optionsPerPlayer: int = Field(default=5, ge=2, le=10)
    answerSec: int = Field(default=45, ge=5, le=600)
    judgeSec: int = Field(default=30, ge=5, le=600)
    maxAnswerLen: int = Field(default=80, ge=10, le=300)
    autoAdvanceSec: int | None = Field(default=None, ge=3, le=300)


def fill(prompt: str, answer: str) -> str:
    return prompt.replace(BLANK, answer, 1) if BLANK in prompt else f"{prompt} {answer}"


class FillBlankHandler(ModeHandler[FillBlankGame]):
    kind = "fill_blank"
    settings_model = FillBlankSettings
    min_players = 3
    needs_answers = True

    def validate_start(self, room: RoomState, settings: Any, content: GameContent) -> None:
        if not content.cards:
            raise GameError("no_content", "this game has no phrases in the selected categories")
        if settings.answerMode == "options" and len(content.answers) < settings.optionsPerPlayer:
            raise GameError("no_content", "not enough answer options in the selected categories")

    def start(
        self, room: RoomState, content: GameContent, settings: Any, ctx: Ctx
    ) -> FillBlankGame:
        deck = list(content.cards)
        ctx.rng.shuffle(deck)
        game = FillBlankGame(
            started_at=ctx.now,
            settings=settings.model_dump(),
            deck=deck[: settings.roundsCount],
            answers_pool=list(content.answers),
            scores=dict.fromkeys(room.active_ids(), 0),
        )
        self._next_round(room, game, ctx)
        return game

    # ---- commands --------------------------------------------------------------------------
    def handle(self, room: RoomState, game: FillBlankGame, cmd: Command, ctx: Ctx) -> None:
        if cmd.type == "blank.submit":
            self._submit(room, game, cmd, ctx)
        elif cmd.type == "blank.pick":
            self.require_phase(game, "judging")
            if cmd.user_id != game.judge:
                raise GameError("not_your_turn", "only the judge picks the winner")
            answer_id = cmd.data.get("answerId")
            if answer_id not in game.order:
                raise GameError("invalid_data", "unknown answerId")
            self._pick(room, game, ctx, str(answer_id), random_pick=False)
        elif cmd.type == "game.next":
            self.require_host(room, cmd)
            if game.phase == "answering":
                self._close_answering(room, game, ctx)
            elif game.phase == "judging":
                self._pick(room, game, ctx, ctx.rng.choice(game.order), random_pick=True)
            else:
                self._next_round(room, game, ctx)
        else:
            super().handle(room, game, cmd, ctx)

    def on_timer(self, room: RoomState, game: FillBlankGame, ctx: Ctx) -> None:
        if game.phase == "answering":
            self._close_answering(room, game, ctx)
        elif game.phase == "judging":
            self._pick(room, game, ctx, ctx.rng.choice(game.order), random_pick=True)
        elif game.phase == "results":
            self._next_round(room, game, ctx)

    def check_progress(self, room: RoomState, game: FillBlankGame, ctx: Ctx) -> None:
        if game.phase == "answering" and self._everyone_submitted(room, game):
            self._close_answering(room, game, ctx)

    def on_player_inactive(
        self, room: RoomState, game: FillBlankGame, user_id: str, ctx: Ctx
    ) -> None:
        if game.phase == "judging" and user_id == game.judge:
            self._pick(room, game, ctx, ctx.rng.choice(game.order), random_pick=True)
            return
        self.check_progress(room, game, ctx)

    # ---- flow ------------------------------------------------------------------------------
    def _answerers(self, room: RoomState, game: FillBlankGame) -> list[str]:
        return [u for u in room.active_ids() if u != game.judge]

    def _everyone_submitted(self, room: RoomState, game: FillBlankGame) -> bool:
        online = [u for u in room.online_ids() if u != game.judge]
        return bool(online) and all(u in game.submissions for u in online)

    def _next_round(self, room: RoomState, game: FillBlankGame, ctx: Ctx) -> None:
        if not game.deck:
            finish_game(room, ctx, "completed")
            return
        card = game.deck.pop(0)
        game.round += 1
        active = room.active_ids()
        game.judge_idx = (game.judge_idx + 1) % len(active)
        game.judge = active[game.judge_idx]
        game.prompt = card.text
        game.submissions = {}
        game.order = []
        game.dealt = self._deal(room, game, ctx) if game.settings["answerMode"] == "options" else {}
        ctx.set_phase(game, "answering", game.settings["answerSec"])
        ctx.emit("blank.round_started", self._round_view(room, game))
        for uid, options in game.dealt.items():
            ctx.emit(
                "blank.options",
                {"round": game.round, "options": [o.model_dump() for o in options]},
                to=[uid],
            )

    def _deal(
        self, room: RoomState, game: FillBlankGame, ctx: Ctx
    ) -> dict[str, list[AnswerOption]]:
        k = game.settings["optionsPerPlayer"]
        players = self._answerers(room, game)
        pool = list(game.answers_pool)
        ctx.rng.shuffle(pool)
        dealt: dict[str, list[AnswerOption]] = {}
        if len(pool) >= k * len(players):
            for i, uid in enumerate(players):
                dealt[uid] = pool[i * k : (i + 1) * k]
        else:  # small pool: distinct per player, may overlap between players
            for uid in players:
                dealt[uid] = ctx.rng.sample(game.answers_pool, k)
        return dealt

    def _submit(self, room: RoomState, game: FillBlankGame, cmd: Command, ctx: Ctx) -> None:
        self.require_phase(game, "answering")
        uid = self.require_active(room, cmd)
        if uid == game.judge:
            raise GameError("not_your_turn", "the judge does not answer")
        if game.settings["answerMode"] == "options":
            option_id = cmd.data.get("optionId")
            option = next((o for o in game.dealt.get(uid, []) if o.id == option_id), None)
            if option is None:
                raise GameError("invalid_data", "optionId is not one of your options")
            text = option.text
        else:
            text = require_str(cmd.data, "text", max_len=game.settings["maxAnswerLen"])
        game.submissions[uid] = BlankSubmission(answer_id="", author_id=uid, text=text)
        ctx.emit(
            "blank.progress",
            {
                "round": game.round,
                "submitted": len(game.submissions),
                "total": len([u for u in room.online_ids() if u != game.judge]),
            },
        )
        if self._everyone_submitted(room, game):
            self._close_answering(room, game, ctx)

    def _close_answering(self, room: RoomState, game: FillBlankGame, ctx: Ctx) -> None:
        if not game.submissions:
            game.history.append({"round": game.round, "winner": None})
            ctx.set_phase(game, "results", game.settings["autoAdvanceSec"])
            ctx.emit(
                "round.results",
                {
                    "round": game.round,
                    "prompt": game.prompt,
                    "winner": None,
                    "reason": "no_answers",
                    "answers": [],
                    "scores": dict(game.scores),
                    "phase": game.phase,
                    "endsAt": game.ends_at,
                },
            )
            return
        authors = list(game.submissions)
        ctx.rng.shuffle(authors)
        game.order = []
        for i, uid in enumerate(authors, start=1):
            game.submissions[uid].answer_id = f"a{i}"
            game.order.append(f"a{i}")
        judge_p = room.player(game.judge or "")
        judge_gone = judge_p is None or not judge_p.active
        ctx.set_phase(game, "judging", game.settings["judgeSec"])
        ctx.emit(
            "blank.judging",
            {
                "round": game.round,
                "prompt": game.prompt,
                "judge": brief(room, game.judge),
                "answers": self._anonymous_answers(game),
                "phase": game.phase,
                "endsAt": game.ends_at,
            },
        )
        if judge_gone:
            self._pick(room, game, ctx, ctx.rng.choice(game.order), random_pick=True)

    def _pick(
        self, room: RoomState, game: FillBlankGame, ctx: Ctx, answer_id: str, *, random_pick: bool
    ) -> None:
        winner = next(s for s in game.submissions.values() if s.answer_id == answer_id)
        game.scores[winner.author_id] = game.scores.get(winner.author_id, 0) + 1
        assert game.prompt is not None
        game.history.append({"round": game.round, "winner": winner.author_id, "judge": game.judge})
        ctx.set_phase(game, "results", game.settings["autoAdvanceSec"])
        ctx.emit(
            "round.results",
            {
                "round": game.round,
                "prompt": game.prompt,
                "judge": brief(room, game.judge),
                "winner": brief(room, winner.author_id),
                "winningAnswerId": answer_id,
                "winningAnswer": winner.text,
                "filled": fill(game.prompt, winner.text),
                "randomPick": random_pick,
                "answers": [
                    {"answerId": s.answer_id, "text": s.text, "author": brief(room, s.author_id)}
                    for s in self._ordered(game)
                ],
                "scores": dict(game.scores),
                "phase": game.phase,
                "endsAt": game.ends_at,
            },
        )

    # ---- views -----------------------------------------------------------------------------
    def _ordered(self, game: FillBlankGame) -> list[BlankSubmission]:
        by_id = {s.answer_id: s for s in game.submissions.values()}
        return [by_id[a] for a in game.order if a in by_id]

    def _anonymous_answers(self, game: FillBlankGame) -> list[dict[str, str]]:
        return [{"answerId": s.answer_id, "text": s.text} for s in self._ordered(game)]

    def _round_view(self, room: RoomState, game: FillBlankGame) -> dict[str, Any]:
        return {
            "round": game.round,
            "roundsLeft": len(game.deck),
            "judge": brief(room, game.judge),
            "prompt": game.prompt,
            "answerMode": game.settings["answerMode"],
            "phase": game.phase,
            "endsAt": game.ends_at,
        }

    def project(self, room: RoomState, game: FillBlankGame, viewer: str) -> dict[str, Any]:
        view = {**self.common_view(game), "current": self._round_view(room, game)}
        mine = game.submissions.get(viewer)
        view["myOptions"] = [o.model_dump() for o in game.dealt.get(viewer, [])]
        view["myAnswer"] = mine.text if mine else None
        view["submitted"] = len(game.submissions)
        if game.phase == "judging":
            view["answers"] = self._anonymous_answers(game)
        return view
