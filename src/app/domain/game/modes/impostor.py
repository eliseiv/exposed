"""Game 4 — impostor.

Every game: random roles; civilians privately get the secret word, impostors get nothing or, with
``hintForImpostor``, a close-but-different word (Spotify → Apple Music). Players explain the word
out loud one by one (``speaking`` phase, current speaker + timer), then everybody alive votes.
The most-voted player is eliminated (a tie eliminates nobody) and their role is revealed.

* all impostors eliminated → civilians win;
* impostors >= civilians, or ``maxRounds`` explained without success → impostors win;
* otherwise another speaking round with the same word.

A match is ``gamesCount`` games; points: +1 to a civilian for each vote cast on an impostor,
+2 to every civilian when civilians win, +3 to every impostor when impostors win.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.domain.game.core import Command, Ctx, GameError, brief
from app.domain.game.lifecycle import finish_game
from app.domain.game.modes.base import ModeHandler, ModeSettings
from app.domain.game.state import GameContent, ImpostorGame, RoomState
from app.domain.game.voting import leaders, tally

CIVILIANS = "civilians"
IMPOSTORS = "impostors"


class ImpostorSettings(ModeSettings):
    impostorCount: int = Field(default=1, ge=1, le=5)
    hintForImpostor: bool = True
    speakSec: int = Field(default=30, ge=5, le=300)
    voteSec: int = Field(default=30, ge=5, le=300)
    gamesCount: int = Field(default=3, ge=1, le=20)
    maxRounds: int = Field(default=3, ge=1, le=10)


class ImpostorHandler(ModeHandler[ImpostorGame]):
    kind = "impostor"
    settings_model = ImpostorSettings
    min_players = 3
    needs_cards = False
    needs_words = True

    def validate_start(self, room: RoomState, settings: Any, content: GameContent) -> None:
        if not content.words:
            raise GameError("no_content", "no words in the selected categories")
        if settings.impostorCount * 2 >= len(room.online_ids()):
            raise GameError("invalid_settings", "impostors must be fewer than half of players")

    def start(self, room: RoomState, content: GameContent, settings: Any, ctx: Ctx) -> ImpostorGame:
        words = list(content.words)
        ctx.rng.shuffle(words)
        game = ImpostorGame(
            started_at=ctx.now,
            settings=settings.model_dump(),
            words=words,
            scores=dict.fromkeys(room.active_ids(), 0),
        )
        self._start_game(room, game, ctx)
        return game

    # ---- commands --------------------------------------------------------------------------
    def handle(self, room: RoomState, game: ImpostorGame, cmd: Command, ctx: Ctx) -> None:
        if cmd.type == "impostor.done_speaking":
            self.require_phase(game, "speaking")
            if cmd.user_id not in (self._speaker(game), room.host_id):
                raise GameError("not_your_turn", "only the speaker or the host")
            self._next_speaker(room, game, ctx)
        elif cmd.type == "impostor.vote":
            self._vote(room, game, cmd, ctx)
        elif cmd.type == "game.next":
            self.require_host(room, cmd)
            if game.phase == "speaking":
                self._next_speaker(room, game, ctx)
            elif game.phase == "voting":
                self._close_voting(room, game, ctx)
            elif game.phase == "vote_result":
                self._start_speaking(room, game, ctx)
            elif game.phase == "game_over":
                if game.game_no >= game.settings["gamesCount"]:
                    finish_game(room, ctx, "completed")
                else:
                    self._start_game(room, game, ctx)
        else:
            super().handle(room, game, cmd, ctx)

    def on_timer(self, room: RoomState, game: ImpostorGame, ctx: Ctx) -> None:
        if game.phase == "speaking":
            self._next_speaker(room, game, ctx)
        elif game.phase == "voting":
            self._close_voting(room, game, ctx)

    def check_progress(self, room: RoomState, game: ImpostorGame, ctx: Ctx) -> None:
        if game.phase == "voting" and self._everyone_voted(room, game):
            self._close_voting(room, game, ctx)

    def on_player_inactive(
        self, room: RoomState, game: ImpostorGame, user_id: str, ctx: Ctx
    ) -> None:
        if user_id not in game.alive:
            return
        was_speaker = game.phase == "speaking" and self._speaker(game) == user_id
        game.alive.remove(user_id)
        game.votes.pop(user_id, None)
        if game.phase in ("speaking", "voting", "vote_result"):
            winner = self._winner(game, rounds_exhausted=False)
            if winner is not None:
                self._game_over(room, game, ctx, winner)
                return
        if was_speaker:
            game.speaker_idx -= 1  # the order list shrinks below; keep the pointer in place
            game.speaking_order.remove(user_id)
            self._next_speaker(room, game, ctx)
            return
        if user_id in game.speaking_order:
            idx = game.speaking_order.index(user_id)
            game.speaking_order.remove(user_id)
            if idx < game.speaker_idx:
                game.speaker_idx -= 1
        self.check_progress(room, game, ctx)

    # ---- flow ------------------------------------------------------------------------------
    def _start_game(self, room: RoomState, game: ImpostorGame, ctx: Ctx) -> None:
        game.game_no += 1
        game.word = game.words[(game.game_no - 1) % len(game.words)]
        players = room.active_ids()
        game.impostors = ctx.rng.sample(players, game.settings["impostorCount"])
        game.alive = list(players)
        game.round = 0
        game.winner = None
        game.votes = {}
        ctx.emit(
            "impostor.game_started",
            {
                "gameNo": game.game_no,
                "gamesCount": game.settings["gamesCount"],
                "impostorCount": len(game.impostors),
                "players": [brief(room, u) for u in game.alive],
            },
        )
        for uid in players:
            ctx.emit("impostor.role", self._role_view(game, uid), to=[uid])
        self._start_speaking(room, game, ctx)

    def _role_view(self, game: ImpostorGame, uid: str) -> dict[str, Any]:
        assert game.word is not None
        if uid in game.impostors:
            hint = game.word.hint if game.settings["hintForImpostor"] else None
            return {"gameNo": game.game_no, "role": "impostor", "word": None, "hint": hint}
        return {"gameNo": game.game_no, "role": "civilian", "word": game.word.word, "hint": None}

    def _speaker(self, game: ImpostorGame) -> str | None:
        if 0 <= game.speaker_idx < len(game.speaking_order):
            return game.speaking_order[game.speaker_idx]
        return None

    def _start_speaking(self, room: RoomState, game: ImpostorGame, ctx: Ctx) -> None:
        game.round += 1
        order = list(game.alive)
        ctx.rng.shuffle(order)
        game.speaking_order = order
        game.speaker_idx = 0
        self._announce_speaker(room, game, ctx)

    def _announce_speaker(self, room: RoomState, game: ImpostorGame, ctx: Ctx) -> None:
        ctx.set_phase(game, "speaking", game.settings["speakSec"])
        ctx.emit("impostor.speaker", self._speaking_view(room, game))

    def _next_speaker(self, room: RoomState, game: ImpostorGame, ctx: Ctx) -> None:
        game.speaker_idx += 1
        if game.speaker_idx < len(game.speaking_order):
            self._announce_speaker(room, game, ctx)
        else:
            self._start_voting(room, game, ctx)

    def _start_voting(self, room: RoomState, game: ImpostorGame, ctx: Ctx) -> None:
        game.votes = {}
        ctx.set_phase(game, "voting", game.settings["voteSec"])
        ctx.emit(
            "impostor.voting",
            {
                "round": game.round,
                "candidates": [brief(room, u) for u in game.alive],
                "phase": game.phase,
                "endsAt": game.ends_at,
            },
        )

    def _vote(self, room: RoomState, game: ImpostorGame, cmd: Command, ctx: Ctx) -> None:
        self.require_phase(game, "voting")
        voter = cmd.user_id
        if voter not in game.alive:
            raise GameError("not_in_game", "eliminated players do not vote")
        target = cmd.data.get("target")
        if target not in game.alive or target == voter:
            raise GameError("invalid_vote", "vote for another player still in the game")
        game.votes[voter] = str(target)
        ctx.emit(
            "vote.progress",
            {"round": game.round, "voted": len(game.votes), "total": len(self._voters(room, game))},
        )
        if self._everyone_voted(room, game):
            self._close_voting(room, game, ctx)

    @staticmethod
    def _voters(room: RoomState, game: ImpostorGame) -> list[str]:
        online = set(room.online_ids())
        return [u for u in game.alive if u in online]

    def _everyone_voted(self, room: RoomState, game: ImpostorGame) -> bool:
        voters = self._voters(room, game)
        return bool(voters) and all(u in game.votes for u in voters)

    def _close_voting(self, room: RoomState, game: ImpostorGame, ctx: Ctx) -> None:
        counts = tally(game.votes, game.alive)
        top = leaders(counts)
        eliminated = top[0] if len(top) == 1 else None
        for voter, target in game.votes.items():
            if target in game.impostors and voter not in game.impostors:
                game.scores[voter] = game.scores.get(voter, 0) + 1
        if eliminated is not None:
            game.alive.remove(eliminated)
        game.history.append(
            {
                "gameNo": game.game_no,
                "round": game.round,
                "votes": dict(game.votes),
                "eliminated": eliminated,
            }
        )
        winner = self._winner(game, rounds_exhausted=game.round >= game.settings["maxRounds"])
        ctx.set_phase(game, "vote_result", None)
        ctx.emit(
            "impostor.vote_result",
            {
                "gameNo": game.game_no,
                "round": game.round,
                "votes": dict(game.votes),
                "counts": counts,
                "tie": len(top) > 1,
                "eliminated": brief(room, eliminated),
                "wasImpostor": eliminated in game.impostors if eliminated else None,
                "scores": dict(game.scores),
                "phase": game.phase,
            },
        )
        if winner is not None:
            self._game_over(room, game, ctx, winner)

    def _winner(self, game: ImpostorGame, *, rounds_exhausted: bool) -> str | None:
        imps = [u for u in game.alive if u in game.impostors]
        civs = [u for u in game.alive if u not in game.impostors]
        if not imps:
            return CIVILIANS
        if len(imps) >= len(civs) or rounds_exhausted:
            return IMPOSTORS
        return None

    def _game_over(self, room: RoomState, game: ImpostorGame, ctx: Ctx, winner: str) -> None:
        assert game.word is not None
        game.winner = winner
        active = set(room.active_ids())
        for uid in active:
            is_imp = uid in game.impostors
            if winner == CIVILIANS and not is_imp:
                game.scores[uid] = game.scores.get(uid, 0) + 2
            elif winner == IMPOSTORS and is_imp:
                game.scores[uid] = game.scores.get(uid, 0) + 3
        ctx.set_phase(game, "game_over", None)
        ctx.emit(
            "impostor.game_over",
            {
                "gameNo": game.game_no,
                "winner": winner,
                "impostors": [brief(room, u) for u in game.impostors],
                "word": game.word.word,
                "hint": game.word.hint,
                "scores": dict(game.scores),
                "isLastGame": game.game_no >= game.settings["gamesCount"],
                "phase": game.phase,
            },
        )

    # ---- views -----------------------------------------------------------------------------
    def _speaking_view(self, room: RoomState, game: ImpostorGame) -> dict[str, Any]:
        return {
            "gameNo": game.game_no,
            "round": game.round,
            "speaker": brief(room, self._speaker(game)),
            "order": [brief(room, u) for u in game.speaking_order],
            "index": game.speaker_idx,
            "phase": game.phase,
            "endsAt": game.ends_at,
        }

    def project(self, room: RoomState, game: ImpostorGame, viewer: str) -> dict[str, Any]:
        view = self.common_view(game)
        view.update(
            {
                "gameNo": game.game_no,
                "alive": [brief(room, u) for u in game.alive],
                "me": self._role_view(game, viewer) if viewer in room.active_ids() else None,
                "myVote": game.votes.get(viewer) if game.phase == "voting" else None,
            }
        )
        if game.phase == "speaking":
            view["speaking"] = self._speaking_view(room, game)
        if game.phase == "game_over" and game.word is not None:
            view["reveal"] = {
                "winner": game.winner,
                "impostors": [brief(room, u) for u in game.impostors],
                "word": game.word.word,
                "hint": game.word.hint,
            }
        return view

    def final_summary(self, room: RoomState, game: ImpostorGame) -> dict[str, Any]:
        return {"gamesPlayed": game.game_no}
