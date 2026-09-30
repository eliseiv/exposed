"""Game mode registry: ``kind`` → handler."""

from __future__ import annotations

from typing import Any

from app.domain.game.core import GameError
from app.domain.game.modes.base import ModeHandler
from app.domain.game.modes.bomb import BombHandler
from app.domain.game.modes.fill_blank import FillBlankHandler
from app.domain.game.modes.hot_seat import HotSeatHandler
from app.domain.game.modes.impostor import ImpostorHandler
from app.domain.game.modes.question_list import QuestionListHandler
from app.domain.game.modes.wheel import WheelHandler

HANDLERS: dict[str, ModeHandler[Any]] = {
    h.kind: h
    for h in (
        QuestionListHandler(),
        WheelHandler(),
        BombHandler(),
        ImpostorHandler(),
        FillBlankHandler(),
        HotSeatHandler(),
    )
}


def get_handler(kind: str) -> ModeHandler[Any]:
    handler = HANDLERS.get(kind)
    if handler is None:
        raise GameError("unknown_mode", f"no game logic for kind '{kind}'")
    return handler
