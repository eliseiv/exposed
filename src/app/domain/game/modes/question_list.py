"""Game 1 — a list of questions everybody votes on (also the generic voting / dare / duel loop).

Every player gets the same card. By card type:

* ``yes_no`` — answer "yes" / "no";
* ``pick_player`` — vote for a player (all players, or ``options_count`` random ones);
* ``duel`` — vote for one of two random players;
* ``dare`` — the vote picks the target, who then gets the dare (``dare.assigned``).

The round closes when every online player voted or the timer ran out. Results show percentages;
unless the card is anonymous, also who voted for what. ``{player}`` in the text is replaced with
a random player's nickname. With ``scoring`` on, every target gets a penalty point.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.domain.game.core import Command, Ctx, GameError, brief
from app.domain.game.lifecycle import finish_game
from app.domain.game.modes.base import ModeHandler, ModeSettings
from app.domain.game.state import GameContent, QuestionListGame, QuestionRound, RoomState
from app.domain.game.voting import leaders, percentages, resolve_tie, tally

PLAYER_PLACEHOLDER = "{player}"
YES_NO = ("yes", "no")


class QuestionListSettings(ModeSettings):
    roundsCount: int | None = Field(default=None, ge=1, le=200)  # None = the whole deck
    voteSec: int = Field(default=20, ge=5, le=300)
    # None = the host moves on manually (`game.next`).
    autoAdvanceSec: int | None = Field(default=None, ge=3, le=300)
    tiePolicy: Literal["random", "all"] = "random"
    scoring: bool = False
    allowSelfVote: bool = True


class QuestionListHandler(ModeHandler[QuestionListGame]):
    kind = "question_list"
    settings_model = QuestionListSettings
    min_players = 3
    scores_ascending = True  # points are penalties of the "losers"

    def validate_start(self, room: RoomState, settings: Any, content: GameContent) -> None:
        if not content.cards:
            raise GameError("no_content", "this game has no cards in the selected categories")

    def start(
        self, room: RoomState, content: GameContent, settings: Any, ctx: Ctx
    ) -> QuestionListGame:
        s: QuestionListSettings = settings
        deck = list(content.cards)
        ctx.rng.shuffle(deck)
        if s.roundsCount is not None:
            deck = deck[: s.roundsCount]
        game = QuestionListGame(
            started_at=ctx.now,
            settings=s.model_dump(),
            deck=deck,
            scores=dict.fromkeys(room.active_ids(), 0) if s.scoring else {},
        )
        self._next_round(room, game, ctx)
        return game

    # ---- commands --------------------------------------------------------------------------
    def handle(self, room: RoomState, game: QuestionListGame, cmd: Command, ctx: Ctx) -> None:
        if cmd.type == "vote.cast":
            self._vote(room, game, cmd, ctx)
        elif cmd.type == "game.next":
            self.require_host(room, cmd)
            if game.phase == "voting":
                self._close_voting(room, game, ctx)  # the host may cut the timer short
            else:
                self._next_round(room, game, ctx)
        else:
            super().handle(room, game, cmd, ctx)

    def on_timer(self, room: RoomState, game: QuestionListGame, ctx: Ctx) -> None:
        if game.phase == "voting":
            self._close_voting(room, game, ctx)
        elif game.phase == "results":
            self._next_round(room, game, ctx)

    def check_progress(self, room: RoomState, game: QuestionListGame, ctx: Ctx) -> None:
        if game.phase == "voting" and self._everyone_voted(room, game):
            self._close_voting(room, game, ctx)

    # ---- flow ------------------------------------------------------------------------------
    def _next_round(self, room: RoomState, game: QuestionListGame, ctx: Ctx) -> None:
        if not game.deck:
            finish_game(room, ctx, "completed")
            return
        card = game.deck.pop(0)
        game.round += 1
        active = room.active_ids()
        subject: str | None = None
        text = card.text
        if PLAYER_PLACEHOLDER in text:
            subject = ctx.rng.choice(active)
            p = room.player(subject)
            text = text.replace(PLAYER_PLACEHOLDER, p.nickname if p else "")

        candidates: list[str] = []
        if card.type == "duel":
            candidates = ctx.rng.sample(active, 2)
        elif card.type in ("pick_player", "dare"):
            n = card.options_count
            candidates = ctx.rng.sample(active, n) if n and n < len(active) else list(active)

        game.current = QuestionRound(
            card=card, text=text, subject_user_id=subject, candidates=candidates
        )
        ctx.set_phase(game, "voting", game.settings["voteSec"])
        ctx.emit("round.started", self._round_view(room, game))

    def _vote(self, room: RoomState, game: QuestionListGame, cmd: Command, ctx: Ctx) -> None:
        self.require_phase(game, "voting")
        voter = self.require_active(room, cmd)
        rnd = game.current
        assert rnd is not None
        choice = cmd.data.get("choice")
        if rnd.card.type == "yes_no":
            if choice not in YES_NO:
                raise GameError("invalid_vote", "choice must be 'yes' or 'no'")
        else:
            if choice not in rnd.candidates:
                raise GameError("invalid_vote", "choice must be one of the candidates")
            if choice == voter and not game.settings["allowSelfVote"]:
                raise GameError("invalid_vote", "you cannot vote for yourself")
        rnd.votes[voter] = str(choice)  # a player may change their vote until the round closes
        ctx.emit(
            "vote.progress",
            {"round": game.round, "voted": len(rnd.votes), "total": len(room.online_ids())},
        )
        if self._everyone_voted(room, game):
            self._close_voting(room, game, ctx)

    @staticmethod
    def _everyone_voted(room: RoomState, game: QuestionListGame) -> bool:
        assert game.current is not None
        online = room.online_ids()
        return bool(online) and all(uid in game.current.votes for uid in online)

    def _close_voting(self, room: RoomState, game: QuestionListGame, ctx: Ctx) -> None:
        rnd = game.current
        assert rnd is not None
        card = rnd.card
        options = list(YES_NO) if card.type == "yes_no" else rnd.candidates
        counts = tally(rnd.votes, options)
        result: dict[str, Any] = {
            "round": game.round,
            "cardId": card.id,
            "type": card.type,
            "text": rnd.text,
            "anonymous": card.is_anonymous,
            "totalVotes": sum(counts.values()),
            "counts": counts,
            "percentages": percentages(counts),
            # who voted for what — hidden on anonymous cards
            "votes": None if card.is_anonymous else dict(rnd.votes),
            "targets": [],
            "points": {},
        }
        if card.type == "yes_no":
            top = leaders(counts)
            result["majority"] = top[0] if len(top) == 1 else None
        else:
            targets = resolve_tie(leaders(counts), game.settings["tiePolicy"], ctx.rng)
            result["tie"] = len(leaders(counts)) > 1
            result["targets"] = [brief(room, uid) for uid in targets]
            if game.settings["scoring"]:
                for uid in targets:
                    game.scores[uid] = game.scores.get(uid, 0) + 1
                result["points"] = dict.fromkeys(targets, 1)
                result["scores"] = dict(game.scores)
            if card.type == "dare" and targets:
                result["dare"] = {"text": rnd.text, "targets": result["targets"]}
        rnd.result = result
        game.history.append({"round": game.round, "cardId": card.id, "counts": counts})
        ctx.set_phase(game, "results", game.settings["autoAdvanceSec"])
        ctx.emit("round.results", {**result, "phase": game.phase, "endsAt": game.ends_at})
        if result.get("dare"):
            # Everyone sees WHO must do it; the target's client shows the dare as "your task".
            ctx.emit("dare.assigned", {"round": game.round, **result["dare"]})

    # ---- views -----------------------------------------------------------------------------
    def _round_view(self, room: RoomState, game: QuestionListGame) -> dict[str, Any]:
        rnd = game.current
        assert rnd is not None
        return {
            "round": game.round,
            "roundsLeft": len(game.deck),
            "phase": game.phase,
            "endsAt": game.ends_at,
            "card": {
                "id": rnd.card.id,
                "type": rnd.card.type,
                "category": rnd.card.category,
                "text": rnd.text,
                "anonymous": rnd.card.is_anonymous,
            },
            "subject": brief(room, rnd.subject_user_id),
            "options": list(YES_NO)
            if rnd.card.type == "yes_no"
            else [brief(room, uid) for uid in rnd.candidates],
        }

    def project(self, room: RoomState, game: QuestionListGame, viewer: str) -> dict[str, Any]:
        view = self.common_view(game)
        if game.current is not None:
            view["current"] = self._round_view(room, game)
            view["myVote"] = game.current.votes.get(viewer)
            view["voted"] = len(game.current.votes)
            view["results"] = game.current.result if game.phase == "results" else None
        return view
