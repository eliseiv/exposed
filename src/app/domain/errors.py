"""Domain error codes on top of the core ``AppError`` hierarchy (one subclass per wire code)."""

from __future__ import annotations

from app.domain.game.core import GameError
from app.errors import AppError, ConflictError, NotFoundError, ValidationFailedError


class InvalidAvatarError(ValidationFailedError):
    code = "invalid_avatar"


class ProfileRequiredError(ConflictError):
    """The player has no nickname yet (``PUT /v1/players/me`` or ``POST /v1/guest`` first)."""

    code = "profile_required"


class RoomNotFoundError(NotFoundError):
    code = "room_not_found"


class ModeNotFoundError(NotFoundError):
    code = "mode_not_found"


class ContentNotFoundError(NotFoundError):
    code = "content_not_found"


class SlugTakenError(ConflictError):
    code = "slug_taken"


# GameError codes → HTTP status for the REST endpoints that run room commands.
_STATUS = {
    "room_not_found": 404,
    "mode_unavailable": 404,
    "room_full": 409,
    "game_in_progress": 409,
    "kicked": 403,
    "busy": 503,
    "invalid_data": 422,
}


class RoomCommandError(AppError):
    """A room command rejected by the engine, surfaced over REST with its own code."""

    def __init__(self, exc: GameError) -> None:
        super().__init__(exc.message)
        self.code = exc.code
        self.status_code = _STATUS.get(exc.code, 409)
