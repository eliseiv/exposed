"""The contract every game mode implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.game.core import Command, Ctx, GameError
from app.domain.game.state import GameBase, GameContent, RoomState

G = TypeVar("G", bound=GameBase)


class ModeSettings(BaseModel):
    """Base of every per-mode settings model. Unknown keys are ignored: when the host switches
    mode, overrides meant for the previous one must not make the new one unstartable."""

    model_config = ConfigDict(extra="ignore")


class ModeHandler(ABC, Generic[G]):
    kind: ClassVar[str]
    settings_model: ClassVar[type[ModeSettings]] = ModeSettings
    # Hard floor of the rules themselves; the catalogue's `min_players` may only raise it.
    min_players: ClassVar[int] = 2
    # What `game.start` needs loaded from the database.
    needs_cards: ClassVar[bool] = True
    needs_words: ClassVar[bool] = False
    needs_answers: ClassVar[bool] = False
    # Leaderboard order: False — more points is better; True — points are penalties.
    scores_ascending: ClassVar[bool] = False

    # ---- settings --------------------------------------------------------------------------
    def parse_settings(self, raw: dict[str, Any]) -> ModeSettings:
        try:
            return self.settings_model.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(p) for p in first.get("loc", ()))
            raise GameError("invalid_settings", f"{field}: {first.get('msg')}") from exc

    def validate_start(self, room: RoomState, settings: Any, content: GameContent) -> None:
        """Raise ``GameError`` when the game cannot start with these players/content."""

    # ---- lifecycle -------------------------------------------------------------------------
    @abstractmethod
    def start(self, room: RoomState, content: GameContent, settings: Any, ctx: Ctx) -> G:
        """Build the game state and emit the opening events (``room.game`` is already None)."""

    def handle(self, room: RoomState, game: G, cmd: Command, ctx: Ctx) -> None:
        raise GameError("invalid_command", f"'{cmd.type}' is not valid in this game")

    def on_timer(self, room: RoomState, game: G, ctx: Ctx) -> None:
        """The phase timer of the CURRENT phase fired."""

    def on_player_inactive(self, room: RoomState, game: G, user_id: str, ctx: Ctx) -> None:
        """A player left the game for good (left / kicked / grace expired)."""
        self.check_progress(room, game, ctx)

    def check_progress(self, room: RoomState, game: G, ctx: Ctx) -> None:
        """Someone went offline: a phase waiting for "everybody" may now be complete."""

    # ---- views -----------------------------------------------------------------------------
    def project(self, room: RoomState, game: G, viewer: str) -> dict[str, Any]:
        """What ``viewer`` may see of the game (snapshots). Never leak hidden info here."""
        return {}

    def final_summary(self, room: RoomState, game: G) -> dict[str, Any]:
        """Extra data for ``game.finished`` (beyond the generic leaderboard)."""
        return {}

    def has_scores(self, game: G) -> bool:
        return bool(game.scores)

    # ---- helpers ---------------------------------------------------------------------------
    @staticmethod
    def require_host(room: RoomState, cmd: Command) -> None:
        if cmd.user_id != room.host_id:
            raise GameError("not_host", "only the host can do this")

    @staticmethod
    def require_phase(game: GameBase, *phases: str) -> None:
        if game.phase not in phases:
            raise GameError("wrong_phase", f"not allowed in phase '{game.phase}'")

    @staticmethod
    def require_active(room: RoomState, cmd: Command) -> str:
        p = room.player(cmd.user_id or "")
        if p is None or not p.active:
            raise GameError("not_in_game", "you are not playing this game")
        return p.user_id

    @staticmethod
    def common_view(game: GameBase) -> dict[str, Any]:
        return {
            "phase": game.phase,
            "phaseId": game.phase_id,
            "endsAt": game.ends_at,
            "round": game.round,
            "settings": game.settings,
            "scores": game.scores,
        }
