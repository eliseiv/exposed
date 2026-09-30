"""Enumerations shared by the database schema and the (pure) game engine."""

from __future__ import annotations

MODE_KINDS = ("question_list", "wheel", "bomb", "impostor", "fill_blank", "hot_seat")
CARD_TYPES = (
    "yes_no",  # answer yes / no
    "pick_player",  # vote for one of the players (all or `options_count` random ones)
    "duel",  # vote for one of two random players
    "dare",  # vote picks the target, who then gets the dare text
    "question",  # a plain question (bomb, hot seat)
    "blank_prompt",  # a phrase with a gap (fill_blank)
)
CATEGORIES = ("friendly", "cringe", "spicy")
