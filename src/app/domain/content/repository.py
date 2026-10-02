"""Read side of the game content: the catalogue and the decks loaded at ``game.start``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.constants import CATEGORIES
from app.domain.game.modes import get_handler
from app.domain.game.state import AnswerOption, CardData, GameContent, ModeInfo, WordData
from app.domain.models import BlankAnswer, Card, GameMode, ImpostorWord

# Kinds whose content does not come from `cards` (players write it / it is word-based).
_KINDS_WITHOUT_CARDS = ("wheel", "impostor")


def mode_info(m: GameMode) -> ModeInfo:
    return ModeInfo(
        id=m.id,
        slug=m.slug,
        kind=m.kind,
        title=m.title,
        min_players=m.min_players,
        max_players=m.max_players,
        default_settings=dict(m.default_settings or {}),
        locale=m.locale,
    )


class ContentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def has_modes(self, locale: str) -> bool:
        found = await self._s.scalar(
            select(GameMode.id)
            .where(GameMode.is_active.is_(True), GameMode.locale == locale)
            .limit(1)
        )
        return found is not None

    async def list_modes(self, locale: str) -> list[tuple[GameMode, dict[str, int]]]:
        """Active modes of one language in catalogue order, with active card counts per
        category."""
        modes = (
            await self._s.scalars(
                select(GameMode)
                .where(GameMode.is_active.is_(True), GameMode.locale == locale)
                .order_by(GameMode.sort_order, GameMode.id)
            )
        ).all()
        rows = (
            await self._s.execute(
                select(Card.mode_id, Card.category, func.count())
                .where(Card.is_active.is_(True))
                .group_by(Card.mode_id, Card.category)
            )
        ).all()
        counts: dict[int, dict[str, int]] = {}
        for mode_id, category, n in rows:
            counts.setdefault(mode_id, {})[category] = n
        return [(m, {c: counts.get(m.id, {}).get(c, 0) for c in CATEGORIES}) for m in modes]

    async def get_mode(self, mode_id: int) -> ModeInfo | None:
        m = await self._s.get(GameMode, mode_id)
        if m is None or not m.is_active:
            return None
        return mode_info(m)

    async def random_mode(self, players: int, locale: str) -> ModeInfo | None:
        has_cards = (
            select(Card.id)
            .where(and_(Card.mode_id == GameMode.id, Card.is_active.is_(True)))
            .exists()
        )
        m = await self._s.scalar(
            select(GameMode)
            .where(
                GameMode.is_active.is_(True),
                GameMode.locale == locale,
                GameMode.min_players <= players,
                GameMode.max_players >= players,
                GameMode.kind.in_(_KINDS_WITHOUT_CARDS) | has_cards,
            )
            .order_by(func.random())
            .limit(1)
        )
        return mode_info(m) if m else None

    async def load_content(self, mode: ModeInfo, categories: list[str], limit: int) -> GameContent:
        handler = get_handler(mode.kind)
        cats = list(categories) or list(CATEGORIES)
        content: dict[str, Any] = {"mode": mode}
        if handler.needs_cards:
            cards = (
                await self._s.scalars(
                    select(Card)
                    .where(
                        Card.mode_id == mode.id,
                        Card.is_active.is_(True),
                        Card.category.in_(cats),
                    )
                    .order_by(func.random())
                    .limit(limit)
                )
            ).all()
            content["cards"] = [
                CardData(
                    id=c.id,
                    type=c.type,
                    category=c.category,
                    text=c.text,
                    is_anonymous=c.is_anonymous,
                    options_count=c.options_count,
                )
                for c in cards
            ]
        if handler.needs_words:
            words = (
                await self._s.scalars(
                    select(ImpostorWord)
                    .where(
                        ImpostorWord.is_active.is_(True),
                        ImpostorWord.category.in_(cats),
                        ImpostorWord.locale == mode.locale,
                    )
                    .order_by(func.random())
                    .limit(limit)
                )
            ).all()
            content["words"] = [WordData(id=w.id, word=w.word, hint=w.hint) for w in words]
        if handler.needs_answers:
            answers = (
                await self._s.scalars(
                    select(BlankAnswer)
                    .where(
                        BlankAnswer.is_active.is_(True),
                        BlankAnswer.category.in_(cats),
                        BlankAnswer.locale == mode.locale,
                    )
                    .order_by(func.random())
                    .limit(limit * 4)
                )
            ).all()
            content["answers"] = [AnswerOption(id=a.id, text=a.text) for a in answers]
        return GameContent.model_validate(content)
