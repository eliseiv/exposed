"""ORM models of the party-game domain (bound to the core ``Base``).

Content (modes, cards, words, answer options, avatars) is editable through the admin API without
a code change: a new "question list" game is one ``game_modes`` row with ``kind='question_list'``
plus its cards. Live room/game state is NOT here — it lives in Redis (see ``realtime/store.py``);
only finished games are archived to ``game_sessions``.

Enumerations are ``TEXT`` + ``CHECK`` rather than PostgreSQL enums: adding a value is then a
one-line constraint swap instead of an ``ALTER TYPE`` dance.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.constants import CARD_TYPES, CATEGORIES, MODE_KINDS
from app.models.base import Base

_now = sa_text("now()")


_LOCALE_CHECK = "locale ~ '^[a-z]{2}$'"


def _locale_column() -> Mapped[str]:
    return mapped_column(Text, nullable=False, server_default=sa_text("'ru'"))


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Avatar(Base):
    """Preset avatar. ``key`` names an asset bundled in the iOS app — no uploads, no storage."""

    __tablename__ = "avatars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))


class PlayerProfile(Base):
    __tablename__ = "player_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    nickname: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("avatars.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    __table_args__ = (
        CheckConstraint("char_length(nickname) BETWEEN 1 AND 24", name="ck_player_nickname_len"),
    )


class GameMode(Base):
    """A game in the catalogue. ``kind`` selects the server logic and the client screen."""

    __tablename__ = "game_modes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Language of the title/description AND of every card of this mode.
    locale: Mapped[str] = _locale_column()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa_text("''"))
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_players: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=sa_text("2")
    )
    max_players: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=sa_text("12")
    )
    default_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    __table_args__ = (
        CheckConstraint(_in("kind", MODE_KINDS), name="ck_game_modes_kind"),
        CheckConstraint(_LOCALE_CHECK, name="ck_game_modes_locale"),
        CheckConstraint(
            "min_players >= 1 AND max_players >= min_players", name="ck_game_modes_players"
        ),
    )


class Card(Base):
    """A question / dare / phrase of one game mode."""

    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mode_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game_modes.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    # May contain the `{player}` placeholder (a random player's nickname is substituted).
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Anonymous card: results show only percentages, never who voted for what.
    is_anonymous: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text("false")
    )
    # pick_player: how many random players are offered (NULL = everybody).
    options_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    __table_args__ = (
        CheckConstraint(_in("type", CARD_TYPES), name="ck_cards_type"),
        CheckConstraint(_in("category", CATEGORIES), name="ck_cards_category"),
        CheckConstraint("char_length(text) BETWEEN 1 AND 500", name="ck_cards_text_len"),
        CheckConstraint(
            "options_count IS NULL OR options_count >= 2", name="ck_cards_options_count"
        ),
        Index("ix_cards_mode_category", "mode_id", "category"),
    )


class ImpostorWord(Base):
    """A secret word for the impostor game + the close-but-different hint for the impostor."""

    __tablename__ = "impostor_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = _locale_column()
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    __table_args__ = (
        CheckConstraint(_in("category", CATEGORIES), name="ck_impostor_words_category"),
        CheckConstraint(_LOCALE_CHECK, name="ck_impostor_words_locale"),
    )


class BlankAnswer(Base):
    """An answer option dealt to players in the fill-the-blank game (``answerMode=options``)."""

    __tablename__ = "blank_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = _locale_column()
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    __table_args__ = (
        CheckConstraint(_in("category", CATEGORIES), name="ck_blank_answers_category"),
        CheckConstraint(_LOCALE_CHECK, name="ck_blank_answers_locale"),
    )


class GameSession(Base):
    """Archive of a finished game (the live state lives in Redis)."""

    __tablename__ = "game_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa_text("gen_random_uuid()")
    )
    room_code: Mapped[str] = mapped_column(Text, nullable=False)
    mode_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("game_modes.id", ondelete="SET NULL"), nullable=True
    )
    mode_kind: Mapped[str] = mapped_column(Text, nullable=False)
    host_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    finish_reason: Mapped[str] = mapped_column(Text, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    players: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'[]'::jsonb")
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_now
    )

    __table_args__ = (Index("ix_game_sessions_finished_at", "finished_at"),)


DOMAIN_TABLES = (
    "game_sessions",
    "cards",
    "impostor_words",
    "blank_answers",
    "player_profiles",
    "game_modes",
    "avatars",
)
