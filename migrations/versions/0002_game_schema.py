"""game schema: avatars, player profiles, game modes, cards, impostor words, blank answers,
game session archive

Mirrors ``src/app/domain/models.py`` exactly (``compare_metadata()`` must stay empty).

Revision ID: 0002_game_schema
Revises: 0001_core_baseline
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_game_schema"
down_revision: str | None = "0001_core_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODE_KINDS = ("question_list", "wheel", "bomb", "impostor", "fill_blank", "hot_seat")
_CARD_TYPES = ("yes_no", "pick_player", "duel", "dare", "question", "blank_prompt")
_CATEGORIES = ("friendly", "cringe", "spicy")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "avatars",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "player_profiles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("nickname", sa.Text(), nullable=False),
        sa.Column(
            "avatar_id",
            sa.Integer(),
            sa.ForeignKey("avatars.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.CheckConstraint("char_length(nickname) BETWEEN 1 AND 24", name="ck_player_nickname_len"),
    )

    op.create_table(
        "game_modes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("min_players", sa.SmallInteger(), nullable=False, server_default=sa.text("2")),
        sa.Column("max_players", sa.SmallInteger(), nullable=False, server_default=sa.text("12")),
        sa.Column(
            "default_settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.CheckConstraint(_in("kind", _MODE_KINDS), name="ck_game_modes_kind"),
        sa.CheckConstraint(
            "min_players >= 1 AND max_players >= min_players", name="ck_game_modes_players"
        ),
    )

    op.create_table(
        "cards",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "mode_id",
            sa.Integer(),
            sa.ForeignKey("game_modes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("options_count", sa.SmallInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.CheckConstraint(_in("type", _CARD_TYPES), name="ck_cards_type"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_cards_category"),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 500", name="ck_cards_text_len"),
        sa.CheckConstraint(
            "options_count IS NULL OR options_count >= 2", name="ck_cards_options_count"
        ),
    )
    op.create_index("ix_cards_mode_category", "cards", ["mode_id", "category"])

    op.create_table(
        "impostor_words",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("word", sa.Text(), nullable=False),
        sa.Column("hint", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_impostor_words_category"),
    )

    op.create_table(
        "blank_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_blank_answers_category"),
    )

    op.create_table(
        "game_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("room_code", sa.Text(), nullable=False),
        sa.Column(
            "mode_id",
            sa.Integer(),
            sa.ForeignKey("game_modes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mode_kind", sa.Text(), nullable=False),
        sa.Column("host_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("finish_reason", sa.Text(), nullable=False),
        sa.Column(
            "settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "players", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
    )
    op.create_index("ix_game_sessions_finished_at", "game_sessions", ["finished_at"])


def downgrade() -> None:
    op.drop_index("ix_game_sessions_finished_at", table_name="game_sessions")
    op.drop_table("game_sessions")
    op.drop_table("blank_answers")
    op.drop_table("impostor_words")
    op.drop_index("ix_cards_mode_category", table_name="cards")
    op.drop_table("cards")
    op.drop_table("game_modes")
    op.drop_table("player_profiles")
    op.drop_table("avatars")
