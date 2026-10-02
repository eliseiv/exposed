"""content language: `locale` on game modes / impostor words / blank answers + English content

Existing rows are Russian (`server_default 'ru'`). Cards inherit the language of their mode, so
the English catalogue is a parallel set of modes (`<slug>_en`) with English cards.
``downgrade()`` removes exactly the English rows seeded here, then the columns.

Revision ID: 0004_locale_and_english
Revises: 0003_seed_content
Create Date: 2026-10-02
"""

# ruff: noqa: E501
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0004_locale_and_english"
down_revision: str | None = "0003_seed_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("game_modes", "impostor_words", "blank_answers")

# slug, kind, title, description, icon, min, max, default_settings, sort
MODES: list[tuple[str, str, str, str, str, int, int, dict[str, Any], int]] = [
    (
        "most_likely_en",
        "question_list",
        "Who's Most Likely To",
        "Vote for the friend most likely to do what the card says.",
        "crown",
        3,
        12,
        {"voteSec": 20},
        10,
    ),
    (
        "yes_no_en",
        "question_list",
        "Yes or No",
        "Answer yes or no — and see what the majority thinks.",
        "check",
        3,
        12,
        {"voteSec": 15},
        20,
    ),
    (
        "duels_en",
        "question_list",
        "Duels",
        "Two players face off — everyone else decides who fits better.",
        "swords",
        3,
        12,
        {"voteSec": 15},
        30,
    ),
    (
        "vote_and_dare_en",
        "question_list",
        "Vote & Dare",
        "The majority picks a victim, the victim does the dare. Penalty points are counted.",
        "fire",
        3,
        12,
        {"voteSec": 20, "scoring": True, "tiePolicy": "all"},
        40,
    ),
    (
        "wheel_en",
        "wheel",
        "Wheel of Fortune",
        "Everyone writes a question, a dare and a gossip — the wheel decides what comes up.",
        "wheel",
        2,
        12,
        {},
        50,
    ),
    (
        "bomb_en",
        "bomb",
        "The Bomb",
        "Answer the question and pass the bomb before it blows up in your hands.",
        "bomb",
        2,
        12,
        {},
        60,
    ),
    (
        "impostor_en",
        "impostor",
        "Impostor",
        "Everyone knows the word except the impostor. Explain it without giving it away.",
        "mask",
        3,
        12,
        {},
        70,
    ),
    (
        "fill_blank_en",
        "fill_blank",
        "Fill in the Blank",
        "Fill the gap with the funniest answer — the judge picks the best one.",
        "pencil",
        3,
        12,
        {},
        80,
    ),
    (
        "hot_seat_en",
        "hot_seat",
        "21 Questions",
        "21 rounds: a random player answers a random question.",
        "chair",
        2,
        12,
        {"roundsCount": 21},
        90,
    ),
]

# slug -> [(type, category, text, is_anonymous, options_count)]
CARDS: dict[str, list[tuple[str, str, str, bool, int | None]]] = {
    "most_likely_en": [
        (
            "pick_player",
            "friendly",
            "Who's most likely to be late to their own wedding?",
            False,
            None,
        ),
        ("pick_player", "friendly", "Who's most likely to become a millionaire?", False, None),
        (
            "pick_player",
            "friendly",
            "Who's most likely to survive a zombie apocalypse?",
            False,
            None,
        ),
        (
            "pick_player",
            "friendly",
            "Who's most likely to get lost in a shopping mall?",
            False,
            None,
        ),
        ("pick_player", "cringe", "Who's most likely to text their ex at 3 a.m.?", True, None),
        (
            "pick_player",
            "cringe",
            "Who's most likely to grab the karaoke mic uninvited?",
            False,
            None,
        ),
        ("pick_player", "cringe", "Who's most likely to cry during a cartoon?", False, None),
        (
            "pick_player",
            "cringe",
            "Who's most likely to photograph their food before tasting it?",
            False,
            None,
        ),
        ("pick_player", "spicy", "Who's most likely to fall for a friend?", True, None),
        ("pick_player", "spicy", "Who's most likely to have a secret romance?", True, None),
        ("pick_player", "spicy", "Who's most likely to kiss on the first date?", True, 3),
    ],
    "yes_no_en": [
        ("yes_no", "friendly", "Do you think {player} has ever gone skydiving?", False, None),
        ("yes_no", "friendly", "Would you take a one-way trip to Mars?", True, None),
        ("yes_no", "friendly", "Do you think {player} can actually cook?", False, None),
        (
            "yes_no",
            "cringe",
            "Have you ever pretended to be on the phone to avoid someone?",
            True,
            None,
        ),
        ("yes_no", "cringe", "Do you think {player} sang in the shower this week?", False, None),
        ("yes_no", "spicy", "Have you ever read someone else's messages?", True, None),
        ("yes_no", "spicy", "Do you think {player} has a crush right now?", True, None),
    ],
    "duels_en": [
        ("duel", "friendly", "Who's the better cook?", False, None),
        ("duel", "friendly", "Who packs for a trip faster?", False, None),
        ("duel", "cringe", "Who sends more voice messages?", False, None),
        ("duel", "cringe", "Who snores louder?", False, None),
        ("duel", "spicy", "Who's the better kisser?", True, None),
    ],
    "vote_and_dare_en": [
        ("dare", "friendly", "Act out any animal until someone guesses it.", False, None),
        ("dare", "friendly", "Tell a joke. If nobody laughs, tell another one.", False, None),
        ("dare", "cringe", "Sing the chorus of any song as dramatically as possible.", False, None),
        ("dare", "cringe", "Show the last photo in your camera roll.", False, None),
        ("dare", "spicy", "Read your latest text message out loud.", False, None),
        ("dare", "spicy", "Call a random contact and tell them you miss them.", False, None),
    ],
    "bomb_en": [
        ("question", "friendly", "Name three capitals in Europe.", False, None),
        ("question", "friendly", "What's your favorite food?", False, None),
        ("question", "friendly", "Name five fruits.", False, None),
        ("question", "friendly", "Where would you travel right now?", False, None),
        ("question", "cringe", "What was your most awkward moment at school?", False, None),
        ("question", "cringe", "What's the worst gift you've ever received?", False, None),
        ("question", "spicy", "Who here do you like the most?", False, None),
        ("question", "spicy", "What's the craziest thing you've done for love?", False, None),
    ],
    "fill_blank_en": [
        ("blank_prompt", "friendly", "The best way to spend a weekend is ___.", False, None),
        ("blank_prompt", "friendly", "My hidden talent is ___.", False, None),
        ("blank_prompt", "cringe", "I'll never admit to my mom that I ___.", False, None),
        ("blank_prompt", "cringe", "The first date went wrong when ___.", False, None),
        ("blank_prompt", "spicy", "My biggest secret is ___.", False, None),
    ],
    "hot_seat_en": [
        ("question", "friendly", "Which superpower do you need the most?", False, None),
        ("question", "friendly", "Which movie could you rewatch forever?", False, None),
        ("question", "friendly", "What did you want to be as a kid?", False, None),
        ("question", "friendly", "Which habit would you like to quit?", False, None),
        ("question", "cringe", "What's the most embarrassing song on your playlist?", False, None),
        ("question", "cringe", "What lie do you tell most often?", False, None),
        ("question", "spicy", "What do you regret the most?", False, None),
        ("question", "spicy", "What would you do if nobody would ever find out?", False, None),
    ],
}

IMPOSTOR_WORDS: list[tuple[str, str, str]] = [
    ("Spotify", "Apple Music", "friendly"),
    ("Pizza", "Burger", "friendly"),
    ("Cat", "Dog", "friendly"),
    ("Sea", "Lake", "friendly"),
    ("Soccer", "Hockey", "friendly"),
    ("Guitar", "Violin", "friendly"),
    ("Airplane", "Helicopter", "friendly"),
    ("Coffee", "Tea", "friendly"),
    ("Instagram", "TikTok", "friendly"),
    ("New Year", "Birthday", "friendly"),
    ("Karaoke", "Concert", "cringe"),
    ("Ex", "Crush", "cringe"),
    ("Selfie", "Story", "cringe"),
    ("Date", "Party", "spicy"),
    ("Kiss", "Hug", "spicy"),
]

BLANK_ANSWERS: list[tuple[str, str]] = (
    [
        (text, "friendly")
        for text in (
            "sleeping until noon",
            "eating pizza in bed",
            "dancing like nobody's watching",
            "singing in the shower",
            "petting other people's cats",
            "rewatching Friends",
            "arguing with the GPS",
            "talking to my plants",
            "buying things I don't need",
            "gaming until sunrise",
            "taking photos of sunsets",
            "collecting fridge magnets",
            "looking for the TV remote",
            "ordering delivery three times a day",
            "hiding from the delivery guy",
            "losing my earbuds",
            "having a picnic on the balcony",
            "learning Spanish from memes",
            "naming the pigeons outside",
            "reading the comments instead of the news",
        )
    ]
    + [
        (text, "cringe")
        for text in (
            "liking my ex's old photo",
            "calling my teacher mom",
            "tripping over nothing",
            "waving at someone who wasn't waving at me",
            "sending a voice message to the wrong chat",
            "mixing up names on a date",
            "falling asleep in a meeting",
            "laughing at the serious moment",
            "crying at a commercial",
            "pretending I read the book",
        )
    ]
    + [
        (text, "spicy")
        for text in (
            "falling for the barista",
            'texting my ex "hey"',
            "escaping a date through the window",
            "kissing in the rain",
            "waking up in someone else's bed",
        )
    ]
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table, sa.Column("locale", sa.Text(), nullable=False, server_default=sa.text("'ru'"))
        )
        op.create_check_constraint(f"ck_{table}_locale", table, "locale ~ '^[a-z]{2}$'")

    conn = op.get_bind()
    for slug, kind, title, desc, icon, mn, mx, settings, sort in MODES:
        conn.execute(
            sa.text(
                "INSERT INTO game_modes (slug, kind, locale, title, description, icon, "
                "min_players, max_players, default_settings, sort_order) VALUES (:slug, :kind, "
                "'en', :title, :d, :icon, :mn, :mx, CAST(:st AS jsonb), :sort)"
            ),
            {
                "slug": slug,
                "kind": kind,
                "title": title,
                "d": desc,
                "icon": icon,
                "mn": mn,
                "mx": mx,
                "st": json.dumps(settings),
                "sort": sort,
            },
        )
    for slug, cards in CARDS.items():
        for card_type, category, text, anonymous, options_count in cards:
            conn.execute(
                sa.text(
                    "INSERT INTO cards (mode_id, type, category, text, is_anonymous, "
                    "options_count) SELECT id, :t, :c, :x, :a, :o FROM game_modes "
                    "WHERE slug = :slug"
                ),
                {
                    "slug": slug,
                    "t": card_type,
                    "c": category,
                    "x": text,
                    "a": anonymous,
                    "o": options_count,
                },
            )
    for word, hint, category in IMPOSTOR_WORDS:
        conn.execute(
            sa.text(
                "INSERT INTO impostor_words (word, hint, category, locale) "
                "VALUES (:w, :h, :c, 'en')"
            ),
            {"w": word, "h": hint, "c": category},
        )
    for text, category in BLANK_ANSWERS:
        conn.execute(
            sa.text("INSERT INTO blank_answers (text, category, locale) VALUES (:t, :c, 'en')"),
            {"t": text, "c": category},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for text, _category in BLANK_ANSWERS:
        conn.execute(
            sa.text("DELETE FROM blank_answers WHERE text = :t AND locale = 'en'"), {"t": text}
        )
    for word, _hint, _category in IMPOSTOR_WORDS:
        conn.execute(
            sa.text("DELETE FROM impostor_words WHERE word = :w AND locale = 'en'"), {"w": word}
        )
    for slug, *_rest in MODES:  # cards cascade with their mode
        conn.execute(sa.text("DELETE FROM game_modes WHERE slug = :s"), {"s": slug})
    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_locale", table, type_="check")
        op.drop_column(table, "locale")
