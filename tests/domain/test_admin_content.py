"""Admin content API: a new question-list game is created and becomes playable without code."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import ADMIN_SECRET

ADMIN = {"X-Admin-Token": ADMIN_SECRET}


async def test_admin_requires_token(gclient: AsyncClient) -> None:
    resp = await gclient.get("/v1/admin/content/modes")
    assert resp.status_code == 401
    resp = await gclient.get("/v1/admin/content/modes", headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401


async def test_create_question_list_game_and_cards(gclient: AsyncClient) -> None:
    mode = await gclient.post(
        "/v1/admin/content/modes",
        headers=ADMIN,
        json={
            "slug": "never_have_i",
            "kind": "question_list",
            "title": "Я никогда не",
            "minPlayers": 3,
            "defaultSettings": {"voteSec": 15},
        },
    )
    assert mode.status_code == 201, mode.text
    mode_id = mode.json()["id"]

    dup = await gclient.post(
        "/v1/admin/content/modes",
        headers=ADMIN,
        json={"slug": "never_have_i", "kind": "question_list", "title": "x", "minPlayers": 3},
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "slug_taken"

    bad_settings = await gclient.post(
        "/v1/admin/content/modes",
        headers=ADMIN,
        json={"slug": "b", "kind": "bomb", "title": "b", "defaultSettings": {"fuseMinSec": 1}},
    )
    assert bad_settings.status_code == 422

    too_few = await gclient.post(
        "/v1/admin/content/modes",
        headers=ADMIN,
        json={"slug": "imp", "kind": "impostor", "title": "i", "minPlayers": 2},
    )
    assert too_few.status_code == 422

    card = await gclient.post(
        "/v1/admin/content/cards",
        headers=ADMIN,
        json={
            "modeId": mode_id,
            "type": "yes_no",
            "category": "cringe",
            "text": "Как ты думаешь, {player} спал на паре?",
        },
    )
    assert card.status_code == 201, card.text
    card_id = card.json()["id"]

    imported = await gclient.post(
        "/v1/admin/content/cards/import",
        headers=ADMIN,
        json={
            "cards": [
                {"modeId": mode_id, "type": "pick_player", "category": "friendly", "text": f"q{i}"}
                for i in range(5)
            ]
        },
    )
    assert imported.json() == {"created": 5}

    missing_mode = await gclient.post(
        "/v1/admin/content/cards",
        headers=ADMIN,
        json={"modeId": 999999, "type": "yes_no", "category": "cringe", "text": "x"},
    )
    assert missing_mode.status_code == 404

    page = await gclient.get(
        "/v1/admin/content/cards", headers=ADMIN, params={"modeId": mode_id, "limit": 2}
    )
    assert page.json()["total"] == 6 and len(page.json()["items"]) == 2
    search = await gclient.get("/v1/admin/content/cards", headers=ADMIN, params={"q": "паре"})
    assert search.json()["total"] == 1

    patched = await gclient.patch(
        f"/v1/admin/content/cards/{card_id}", headers=ADMIN, json={"isAnonymous": True}
    )
    assert patched.json()["isAnonymous"] is True

    catalog = (await gclient.get("/v1/modes")).json()
    entry = next(m for m in catalog if m["id"] == mode_id)
    assert entry["kind"] == "question_list"
    assert entry["cardCounts"] == {"friendly": 5, "cringe": 1, "spicy": 0}

    # hide from the catalogue, then delete
    await gclient.patch(
        f"/v1/admin/content/modes/{mode_id}", headers=ADMIN, json={"isActive": False}
    )
    assert all(m["id"] != mode_id for m in (await gclient.get("/v1/modes")).json())
    assert (
        await gclient.delete(f"/v1/admin/content/cards/{card_id}", headers=ADMIN)
    ).status_code == 204
    assert (
        await gclient.delete(f"/v1/admin/content/modes/{mode_id}", headers=ADMIN)
    ).status_code == 204
    all_modes = (await gclient.get("/v1/admin/content/modes", headers=ADMIN)).json()
    assert all(m["id"] != mode_id for m in all_modes)
    gone = await gclient.patch(f"/v1/admin/content/modes/{mode_id}", headers=ADMIN, json={})
    assert gone.status_code == 404


async def test_words_answers_avatars_crud(gclient: AsyncClient) -> None:
    for path, body, patch in (
        ("impostor-words", {"word": "Spotify", "hint": "Apple Music"}, {"hint": "Deezer"}),
        ("blank-answers", {"text": "спать до обеда"}, {"category": "spicy"}),
        ("avatars", {"key": "avatar_cat"}, {"sortOrder": 5}),
    ):
        created = await gclient.post(f"/v1/admin/content/{path}", headers=ADMIN, json=body)
        assert created.status_code == 201, created.text
        obj_id = created.json()["id"]
        updated = await gclient.patch(
            f"/v1/admin/content/{path}/{obj_id}", headers=ADMIN, json=patch
        )
        assert updated.status_code == 200
        for key, value in patch.items():
            assert updated.json()[key] == value
        listed = (await gclient.get(f"/v1/admin/content/{path}", headers=ADMIN)).json()
        items = listed["items"] if isinstance(listed, dict) else listed
        assert any(i["id"] == obj_id for i in items)
        if path != "avatars":
            deleted = await gclient.delete(f"/v1/admin/content/{path}/{obj_id}", headers=ADMIN)
            assert deleted.status_code == 204
            missing = await gclient.delete(f"/v1/admin/content/{path}/{obj_id}", headers=ADMIN)
            assert missing.status_code == 404
    filtered = await gclient.get(
        "/v1/admin/content/impostor-words", headers=ADMIN, params={"category": "spicy"}
    )
    assert filtered.json()["total"] == 0
