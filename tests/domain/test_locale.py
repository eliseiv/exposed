"""Content language: Accept-Language parsing, catalogue / room / decks per locale."""

from __future__ import annotations

import contextlib
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.game.core import GameError
from app.domain.locale import parse_accept_language, resolve_locale
from app.domain.models import BlankAnswer, Card, GameMode, ImpostorWord
from tests.conftest import ADMIN_SECRET
from tests.domain.conftest import WsPlayer, connect, guest
from tests.domain.helpers import make_sim, mode

ADMIN = {"X-Admin-Token": ADMIN_SECRET}
EN = {"Accept-Language": "en-US,en;q=0.9"}


# ---- pure ------------------------------------------------------------------------------------
def test_parse_accept_language_respects_weights() -> None:
    assert parse_accept_language("ru;q=0.5, en-GB, de;q=0.8, *;q=0.1") == ["en", "de", "ru"]
    assert parse_accept_language("fr;q=0") == []
    assert parse_accept_language(None) == []


def test_resolve_locale_prefers_explicit_then_header_then_default() -> None:
    supported = ("ru", "en")
    assert resolve_locale("en", "ru", supported, "ru") == "en"
    assert resolve_locale(None, "de, en;q=0.5", supported, "ru") == "en"
    assert resolve_locale("de", "fr", supported, "ru") == "ru"


def test_choosing_a_game_sets_the_room_language_and_switching_language_drops_it() -> None:
    sim = make_sim(3)
    en_mode = mode("bomb").model_copy(update={"locale": "en"})
    sim.run("room.update_settings", sim.host, mode=en_mode.model_dump())
    assert sim.room.locale == "en"
    sim.run("room.update_settings", sim.host, locale="ru")
    assert sim.room.locale == "ru" and sim.room.mode is None
    assert sim.last_event("room.settings").data["locale"] == "ru"
    with pytest.raises(GameError):
        sim.run("room.update_settings", sim.host, locale="english")


# ---- over the wire ---------------------------------------------------------------------------
@pytest.fixture
async def bilingual(sessionmaker_: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """An impostor game and a hot seat game in each language, plus words / answers."""
    ids: dict[str, int] = {}
    async with sessionmaker_() as s:
        for locale in ("ru", "en"):
            for kind, min_players in (("impostor", 3), ("hot_seat", 2)):
                m = GameMode(
                    slug=f"{kind}_{locale}",
                    kind=kind,
                    locale=locale,
                    title=f"{kind} {locale}",
                    min_players=min_players,
                )
                s.add(m)
                await s.flush()
                ids[f"{kind}_{locale}"] = m.id
                if kind == "hot_seat":
                    s.add(Card(mode_id=m.id, type="question", category="friendly", text=locale))
            s.add_all(
                ImpostorWord(word=f"{locale}-word{i}", hint="h", category="friendly", locale=locale)
                for i in range(3)
            )
            s.add(BlankAnswer(text=f"{locale}-answer", category="friendly", locale=locale))
        await s.commit()
    return ids


async def test_catalog_follows_accept_language_with_fallback(
    gclient: AsyncClient, bilingual: dict[str, int]
) -> None:
    ru = await gclient.get("/v1/modes")
    assert ru.headers["content-language"] == "ru"
    assert {m["locale"] for m in ru.json()} == {"ru"}
    en = await gclient.get("/v1/modes", headers=EN)
    assert en.headers["content-language"] == "en"
    assert {m["slug"] for m in en.json()} == {"impostor_en", "hot_seat_en"}
    assert (await gclient.get("/v1/modes", params={"locale": "en"})).json() == en.json()
    unsupported = await gclient.get("/v1/modes", headers={"Accept-Language": "de"})
    assert unsupported.headers["content-language"] == "ru"


async def test_catalog_falls_back_when_language_has_no_games(
    gclient: AsyncClient, content: dict[str, int]
) -> None:
    resp = await gclient.get("/v1/modes", headers=EN)  # `content` seeds only `ru` games
    assert resp.headers["content-language"] == "ru" and resp.json()


async def _start(gclient: AsyncClient, n: int, body: dict[str, Any], headers: dict[str, str]):  # type: ignore[no-untyped-def]
    players = [await guest(gclient, f"P{i}") for i in range(n)]
    created = await gclient.post("/v1/rooms", json=body, headers={**players[0][1], **headers})
    assert created.status_code == 201, created.text
    code = created.json()["code"]
    for _uid, h in players[1:]:
        await gclient.post(f"/v1/rooms/{code}/join", headers=h)
    return code, players, created.json()["room"]


async def test_room_language_drives_random_game_and_decks(
    gclient: AsyncClient, bilingual: dict[str, int]
) -> None:
    code, players, room = await _start(gclient, 3, {}, EN)
    assert room["locale"] == "en" and room["mode"] is None
    async with contextlib.AsyncExitStack() as stack:
        sockets = []
        for uid, h in players:
            p = WsPlayer(await stack.enter_async_context(connect(gclient, code, h)), uid)
            await p.recv()
            sockets.append(p)
        host = sockets[0]
        assert (await host.command("room.update_settings", locale="de"))["type"] == "error"
        await host.command(
            "room.update_settings", modeId=bilingual["impostor_en"], settings={"gamesCount": 1}
        )
        await host.command("game.start")
        roles = [(await p.wait_for("impostor.role"))["data"] for p in sockets]
        words = {r["word"] for r in roles if r["word"]}
        assert words and all(w.startswith("en-") for w in words)


async def test_explicit_room_locale_and_mode_locale_wins(
    gclient: AsyncClient, bilingual: dict[str, int]
) -> None:
    _code, _players, room = await _start(gclient, 1, {"locale": "en"}, {})
    assert room["locale"] == "en"
    _code, _players, room = await _start(
        gclient, 1, {"modeId": bilingual["hot_seat_ru"], "locale": "en"}, EN
    )
    assert room["locale"] == "ru" and room["mode"]["locale"] == "ru"


async def test_admin_content_carries_locale(gclient: AsyncClient) -> None:
    created = await gclient.post(
        "/v1/admin/content/modes",
        headers=ADMIN,
        json={
            "slug": "nhie_en",
            "kind": "question_list",
            "locale": "en",
            "title": "Never Have I",
            "minPlayers": 3,
        },
    )
    assert created.status_code == 201 and created.json()["locale"] == "en"
    bad = await gclient.post(
        "/v1/admin/content/impostor-words", headers=ADMIN, json={"word": "x", "locale": "de"}
    )
    assert bad.status_code == 422
    word = await gclient.post(
        "/v1/admin/content/impostor-words", headers=ADMIN, json={"word": "Pizza", "locale": "en"}
    )
    assert word.json()["locale"] == "en"
    listed = await gclient.get(
        "/v1/admin/content/impostor-words", headers=ADMIN, params={"locale": "ru"}
    )
    assert listed.json()["total"] == 0
    modes = await gclient.get("/v1/admin/content/modes", headers=ADMIN, params={"locale": "en"})
    assert [m["slug"] for m in modes.json()] == ["nhie_en"]
