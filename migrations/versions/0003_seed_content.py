"""seed content: avatars, the game catalogue, starter cards / words / answer options

Starter content only — the real decks are managed through the admin API
(``/v1/admin/content/*``). ``downgrade()`` removes exactly the rows seeded here (by slug / key /
text), never content added later by an operator.

Revision ID: 0003_seed_content
Revises: 0002_game_schema
Create Date: 2026-09-30
"""

# ruff: noqa: E501
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0003_seed_content"
down_revision: str | None = "0002_game_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AVATARS = [f"avatar_{i:02d}" for i in range(1, 13)]

# slug, kind, title, description, icon, min, max, default_settings, sort
MODES: list[tuple[str, str, str, str, str, int, int, dict[str, Any], int]] = [
    (
        "most_likely",
        "question_list",
        "Кто вероятнее всего",
        "Голосуйте, кто из компании скорее всего сделает то, что написано на карточке.",
        "crown",
        3,
        12,
        {"voteSec": 20},
        10,
    ),
    (
        "yes_no",
        "question_list",
        "Да или нет",
        "Отвечайте да или нет — и смотрите, что думает большинство.",
        "check",
        3,
        12,
        {"voteSec": 15},
        20,
    ),
    (
        "duels",
        "question_list",
        "Дуэли",
        "Двое против друг друга — остальные решают, кто подходит больше.",
        "swords",
        3,
        12,
        {"voteSec": 15},
        30,
    ),
    (
        "vote_and_dare",
        "question_list",
        "Голосуй и выполняй",
        "Большинство выбирает жертву, а жертва выполняет задание. Считаем штрафные очки.",
        "fire",
        3,
        12,
        {"voteSec": 20, "scoring": True, "tiePolicy": "all"},
        40,
    ),
    (
        "wheel",
        "wheel",
        "Колесо фортуны",
        "Каждый пишет вопрос, задание и сплетню, а колесо решает, что выпадет.",
        "wheel",
        2,
        12,
        {},
        50,
    ),
    (
        "bomb",
        "bomb",
        "Бомба",
        "Отвечай на вопрос и передавай бомбу, пока она не взорвалась у тебя в руках.",
        "bomb",
        2,
        12,
        {},
        60,
    ),
    (
        "impostor",
        "impostor",
        "Импостер",
        "Все знают слово, кроме импостера. Объясняйте так, чтобы он не догадался.",
        "mask",
        3,
        12,
        {},
        70,
    ),
    (
        "fill_blank",
        "fill_blank",
        "Допиши фразу",
        "Заполните пропуск самым смешным вариантом — судья выберет лучший.",
        "pencil",
        3,
        12,
        {},
        80,
    ),
    (
        "hot_seat",
        "hot_seat",
        "21 вопрос",
        "21 раунд: случайный игрок отвечает на случайный вопрос.",
        "chair",
        2,
        12,
        {"roundsCount": 21},
        90,
    ),
]

# slug -> [(type, category, text, is_anonymous, options_count)]
CARDS: dict[str, list[tuple[str, str, str, bool, int | None]]] = {
    "most_likely": [
        (
            "pick_player",
            "friendly",
            "Кто вероятнее всего опоздает на собственную свадьбу?",
            False,
            None,
        ),
        ("pick_player", "friendly", "Кто вероятнее всего станет миллионером?", False, None),
        (
            "pick_player",
            "friendly",
            "Кто вероятнее всего переживёт зомби-апокалипсис?",
            False,
            None,
        ),
        (
            "pick_player",
            "friendly",
            "Кто вероятнее всего заблудится в торговом центре?",
            False,
            None,
        ),
        ("pick_player", "cringe", "Кто вероятнее всего напишет бывшему в 3 часа ночи?", True, None),
        (
            "pick_player",
            "cringe",
            "Кто вероятнее всего споёт в караоке без приглашения?",
            False,
            None,
        ),
        ("pick_player", "cringe", "Кто вероятнее всего расплачется на мультфильме?", False, None),
        (
            "pick_player",
            "cringe",
            "Кто вероятнее всего сфоткает еду раньше, чем попробует?",
            False,
            None,
        ),
        ("pick_player", "spicy", "Кто вероятнее всего влюбится в друга?", True, None),
        ("pick_player", "spicy", "Кто вероятнее всего скрывает тайный роман?", True, None),
        ("pick_player", "spicy", "Кто вероятнее всего поцелуется на первом свидании?", True, 3),
    ],
    "yes_no": [
        (
            "yes_no",
            "friendly",
            "Как ты думаешь, {player} когда-нибудь прыгал с парашютом?",
            False,
            None,
        ),
        ("yes_no", "friendly", "Ты бы полетел на Марс в один конец?", True, None),
        ("yes_no", "friendly", "Как ты думаешь, {player} умеет готовить?", False, None),
        ("yes_no", "cringe", "Ты когда-нибудь притворялся, что говоришь по телефону?", True, None),
        ("yes_no", "cringe", "Как ты думаешь, {player} пел в душе на этой неделе?", False, None),
        ("yes_no", "spicy", "Ты когда-нибудь читал чужую переписку?", True, None),
        ("yes_no", "spicy", "Как ты думаешь, {player} сейчас в кого-то влюблён?", True, None),
    ],
    "duels": [
        ("duel", "friendly", "Кто лучше готовит?", False, None),
        ("duel", "friendly", "Кто быстрее соберётся в поездку?", False, None),
        ("duel", "cringe", "Кто чаще отправляет голосовые?", False, None),
        ("duel", "cringe", "Кто громче храпит?", False, None),
        ("duel", "spicy", "Кто лучше целуется?", True, None),
    ],
    "vote_and_dare": [
        ("dare", "friendly", "Изобрази любое животное, пока все не угадают.", False, None),
        ("dare", "friendly", "Расскажи анекдот. Если никто не засмеётся — ещё один.", False, None),
        ("dare", "cringe", "Спой припев любой песни максимально драматично.", False, None),
        ("dare", "cringe", "Покажи последнее фото в галерее.", False, None),
        ("dare", "spicy", "Прочитай вслух последнее сообщение в своём телефоне.", False, None),
        ("dare", "spicy", "Позвони случайному контакту и скажи, что скучаешь.", False, None),
    ],
    "bomb": [
        ("question", "friendly", "Назови три столицы Европы.", False, None),
        ("question", "friendly", "Какое твоё любимое блюдо?", False, None),
        ("question", "friendly", "Назови пять фруктов.", False, None),
        ("question", "friendly", "Куда бы ты поехал прямо сейчас?", False, None),
        ("question", "cringe", "Самая неловкая ситуация в школе?", False, None),
        ("question", "cringe", "Худший подарок, который ты получал?", False, None),
        ("question", "spicy", "Кто из присутствующих тебе нравится больше всех?", False, None),
        ("question", "spicy", "Самое безумное, что ты делал ради любви?", False, None),
    ],
    "fill_blank": [
        ("blank_prompt", "friendly", "Лучший способ провести выходные — это ___.", False, None),
        ("blank_prompt", "friendly", "Мой главный талант — ___.", False, None),
        ("blank_prompt", "cringe", "Я никогда не признаюсь маме, что ___.", False, None),
        ("blank_prompt", "cringe", "Первое свидание пошло не так, когда ___.", False, None),
        ("blank_prompt", "spicy", "Мой самый большой секрет — ___.", False, None),
    ],
    "hot_seat": [
        ("question", "friendly", "Какая суперспособность тебе нужна больше всего?", False, None),
        ("question", "friendly", "Какой фильм ты можешь пересматривать бесконечно?", False, None),
        ("question", "friendly", "Кем ты мечтал стать в детстве?", False, None),
        ("question", "friendly", "Какую привычку ты хотел бы бросить?", False, None),
        ("question", "cringe", "Самый стыдный трек в твоём плейлисте?", False, None),
        ("question", "cringe", "Какую ложь ты говоришь чаще всего?", False, None),
        ("question", "spicy", "О чём ты жалеешь больше всего?", False, None),
        ("question", "spicy", "Что бы ты сделал, если бы никто никогда не узнал?", False, None),
    ],
}

IMPOSTOR_WORDS: list[tuple[str, str, str]] = [
    ("Spotify", "Apple Music", "friendly"),
    ("Пицца", "Бургер", "friendly"),
    ("Кошка", "Собака", "friendly"),
    ("Море", "Озеро", "friendly"),
    ("Футбол", "Хоккей", "friendly"),
    ("Гитара", "Скрипка", "friendly"),
    ("Самолёт", "Вертолёт", "friendly"),
    ("Кофе", "Чай", "friendly"),
    ("Instagram", "TikTok", "friendly"),
    ("Новый год", "День рождения", "friendly"),
    ("Караоке", "Концерт", "cringe"),
    ("Бывший", "Краш", "cringe"),
    ("Селфи", "Сторис", "cringe"),
    ("Свидание", "Тусовка", "spicy"),
    ("Поцелуй", "Объятия", "spicy"),
]

BLANK_ANSWERS: list[tuple[str, str]] = (
    [
        (text, "friendly")
        for text in (
            "спать до обеда",
            "есть пиццу в кровати",
            "танцевать как никто не видит",
            "петь в душе",
            "гладить чужих котов",
            "пересматривать «Друзей»",
            "спорить с навигатором",
            "разговаривать с растениями",
            "покупать ненужные вещи",
            "играть в приставку до утра",
            "фоткать закаты",
            "собирать магнитики",
            "искать пульт от телевизора",
            "заказывать доставку три раза в день",
            "прятаться от курьера",
            "терять наушники",
            "устраивать пикник на балконе",
            "учить испанский по мемам",
            "придумывать клички голубям",
            "читать комментарии вместо новостей",
        )
    ]
    + [
        (text, "cringe")
        for text in (
            "лайкнуть старое фото бывшего",
            "назвать учителя мамой",
            "споткнуться на ровном месте",
            "помахать человеку, который махал не мне",
            "отправить голосовое не тому",
            "перепутать имя на свидании",
            "уснуть на совещании",
            "рассмеяться на серьёзном моменте",
            "плакать под рекламу",
            "врать, что прочитал книгу",
        )
    ]
    + [
        (text, "spicy")
        for text in (
            "влюбиться в бариста",
            "написать бывшему «привет»",
            "сбежать со свидания через окно",
            "целоваться под дождём",
            "проснуться не в своей кровати",
        )
    ]
)


def upgrade() -> None:
    conn = op.get_bind()
    for i, key in enumerate(AVATARS):
        conn.execute(
            sa.text("INSERT INTO avatars (key, sort_order) VALUES (:k, :s)"), {"k": key, "s": i}
        )

    for slug, kind, title, desc, icon, mn, mx, settings, sort in MODES:
        conn.execute(
            sa.text(
                "INSERT INTO game_modes (slug, kind, title, description, icon, min_players, "
                "max_players, default_settings, sort_order) VALUES (:slug, :kind, :title, :d, "
                ":icon, :mn, :mx, CAST(:st AS jsonb), :sort)"
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
            sa.text("INSERT INTO impostor_words (word, hint, category) VALUES (:w, :h, :c)"),
            {"w": word, "h": hint, "c": category},
        )

    for text, category in BLANK_ANSWERS:
        conn.execute(
            sa.text("INSERT INTO blank_answers (text, category) VALUES (:t, :c)"),
            {"t": text, "c": category},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for text, _category in BLANK_ANSWERS:
        conn.execute(sa.text("DELETE FROM blank_answers WHERE text = :t"), {"t": text})
    for word, _hint, _category in IMPOSTOR_WORDS:
        conn.execute(sa.text("DELETE FROM impostor_words WHERE word = :w"), {"w": word})
    # cards cascade with their mode
    for slug, *_rest in MODES:
        conn.execute(sa.text("DELETE FROM game_modes WHERE slug = :s"), {"s": slug})
    for key in AVATARS:
        conn.execute(sa.text("DELETE FROM avatars WHERE key = :k"), {"k": key})
