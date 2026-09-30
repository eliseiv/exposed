"""End-to-end over REST + WebSocket on real PostgreSQL and Redis."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.domain.conftest import FakeClock, WsPlayer, closed_code, connect, guest


async def _room_with(
    gclient: AsyncClient, n: int, mode_id: int | None
) -> tuple[str, list[tuple[str, dict[str, str]]]]:
    players = [await guest(gclient, f"P{i}") for i in range(1, n + 1)]
    resp = await gclient.post("/v1/rooms", json={"modeId": mode_id}, headers=players[0][1])
    assert resp.status_code == 201, resp.text
    code = resp.json()["code"]
    for _uid, headers in players[1:]:
        joined = await gclient.post(f"/v1/rooms/{code}/join", headers=headers)
        assert joined.status_code == 200, joined.text
    return code, players


async def test_guest_login_profile_and_catalog(gclient: AsyncClient, content: dict) -> None:
    uid, headers = await guest(gclient, "Alice")
    me = await gclient.get("/v1/players/me", headers=headers)
    assert me.json()["nickname"] == "Alice"
    avatars = (await gclient.get("/v1/avatars")).json()
    upd = await gclient.put(
        "/v1/players/me", json={"nickname": "Alicia", "avatarId": avatars[0]["id"]}, headers=headers
    )
    assert upd.json()["avatarKey"] == avatars[0]["key"]
    bad = await gclient.put(
        "/v1/players/me", json={"nickname": "A", "avatarId": 99999}, headers=headers
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_avatar"
    modes = (await gclient.get("/v1/modes")).json()
    assert {m["kind"] for m in modes} == set(content)
    ql = next(m for m in modes if m["kind"] == "question_list")
    assert ql["cardCounts"]["friendly"] == 3


async def test_join_errors(gclient: AsyncClient, content: dict) -> None:
    _uid, headers = await guest(gclient, "Bob")
    missing = await gclient.post("/v1/rooms/ZZZZ/join", headers=headers)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "room_not_found"
    peek = await gclient.get("/v1/rooms/ZZZZ", headers=headers)
    assert peek.status_code == 404


async def test_full_question_list_round_over_websockets(
    gclient: AsyncClient,
    content: dict,
    runtime: Any,
    clock: FakeClock,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    code, players = await _room_with(gclient, 3, content["question_list"])
    async with contextlib.AsyncExitStack() as stack:
        sockets: list[WsPlayer] = []
        for uid, headers in players:
            ws = await stack.enter_async_context(connect(gclient, code, headers))
            p = WsPlayer(ws, uid)
            snap = await p.recv()
            assert snap["type"] == "room.snapshot"
            assert snap["data"]["code"] == code
            sockets.append(p)
        host = sockets[0]
        # everybody sees the others connect
        await host.wait_for("player.connected")

        reply = await sockets[1].command("game.start")
        assert reply["type"] == "error" and reply["data"]["code"] == "not_host"

        assert (await host.command("game.start"))["type"] == "ack"
        for p in sockets:
            started = await p.wait_for("round.started")
        candidates = [o["userId"] for o in started["data"]["options"]]
        target = candidates[0]
        for p in sockets:
            assert (await p.command("vote.cast", choice=target))["type"] == "ack"
        for p in sockets:
            results = await p.wait_for("round.results")
            assert results["data"]["targets"][0]["userId"] == target
            assert results["data"]["targets"][0]["nickname"].startswith("P")

        # host moves on; the next round's timer expires with no votes
        await host.command("game.next")
        await host.wait_for("round.started")
        clock.advance(25)
        assert await runtime.fire_due_timers() >= 1
        res = await host.wait_for("round.results")
        assert res["data"]["totalVotes"] == 0

        await host.command("game.end")
        finished = await sockets[2].wait_for("game.finished")
        assert finished["data"]["reason"] == "host_ended"

    async with sessionmaker_() as s:
        row = (await s.execute(text("SELECT mode_kind, finish_reason FROM game_sessions"))).one()
    assert tuple(row) == ("question_list", "host_ended")


async def test_private_events_reach_only_their_recipient(
    gclient: AsyncClient, content: dict
) -> None:
    code, players = await _room_with(gclient, 4, content["impostor"])
    async with contextlib.AsyncExitStack() as stack:
        sockets = []
        for uid, headers in players:
            p = WsPlayer(await stack.enter_async_context(connect(gclient, code, headers)), uid)
            await p.recv()
            sockets.append(p)
        await sockets[0].command("game.start")
        roles = [await p.wait_for("impostor.role") for p in sockets]
        assert sum(1 for r in roles if r["data"]["role"] == "impostor") == 1
        for p in sockets:
            await p.wait_for("impostor.speaker")
            assert sum(1 for m in p.log if m["type"] == "impostor.role") == 1


async def test_disconnect_grace_and_reconnect_snapshot(
    gclient: AsyncClient, content: dict, runtime: Any, clock: FakeClock
) -> None:
    code, players = await _room_with(gclient, 3, content["question_list"])
    (host_id, host_h), (u2, h2), (u3, h3) = players
    async with connect(gclient, code, host_h) as host_ws:
        host = WsPlayer(host_ws, host_id)
        await host.recv()
        async with connect(gclient, code, h2) as ws2:
            await WsPlayer(ws2, u2).recv()
            await host.wait_for("player.connected")
        dropped = await host.wait_for("player.disconnected")
        assert dropped["data"]["userId"] == u2

        # reconnect inside the grace window: seat kept, fresh snapshot
        async with connect(gclient, code, h2) as ws2:
            again = WsPlayer(ws2, u2)
            snap = await again.recv()
            assert snap["type"] == "room.snapshot"
            assert any(p["userId"] == u2 for p in snap["data"]["players"])
        await host.wait_for("player.disconnected")

        # u3 never connected; u2 drops for good → removed after the grace period
        clock.advance(31)
        await runtime.fire_due_timers()
        left = await host.wait_for("player.left")
        assert left["data"] == {"userId": u2, "reason": "timeout"}
        room = (await gclient.get(f"/v1/rooms/{code}", headers=host_h)).json()["room"]
        assert [p["userId"] for p in room["players"]] == [host_id, u3]


async def test_newer_connection_replaces_older_and_kick_closes_socket(
    gclient: AsyncClient, content: dict
) -> None:
    code, players = await _room_with(gclient, 3, None)
    (host_id, host_h), (u2, h2), _ = players
    async with connect(gclient, code, host_h) as host_ws:
        host = WsPlayer(host_ws, host_id)
        await host.recv()
        async with connect(gclient, code, h2) as first:
            await WsPlayer(first, u2).recv()
            async with connect(gclient, code, h2) as second:
                await WsPlayer(second, u2).recv()
                closed = await closed_code(first)
                assert closed == 4001
                await host.command("room.kick", userId=u2)
                assert await closed_code(second) == 4003
        rejoin = await gclient.post(f"/v1/rooms/{code}/join", headers=h2)
        assert rejoin.status_code == 403 and rejoin.json()["error"]["code"] == "kicked"


async def test_ws_rejects_bad_token_and_non_member(gclient: AsyncClient, content: dict) -> None:
    code, _players = await _room_with(gclient, 1, None)
    async with connect(gclient, code, {"Authorization": "Bearer nope"}) as ws:
        assert await closed_code(ws) == 4401
    _uid, outsider = await guest(gclient, "Mallory")
    async with connect(gclient, code, outsider) as ws:
        err = json.loads(await ws.recv())
        assert err["data"]["code"] == "not_in_room"
        assert await closed_code(ws) == 4004


async def test_unknown_and_system_commands_are_rejected(
    gclient: AsyncClient, content: dict
) -> None:
    code, players = await _room_with(gclient, 1, None)
    uid, headers = players[0]
    async with connect(gclient, code, headers) as ws:
        p = WsPlayer(ws, uid)
        await p.recv()
        for forbidden in ("timer", "presence.connect", "room.join", "nope"):
            reply = await p.command(forbidden)
            assert reply["data"]["code"] == "unknown_command"
        await ws.send("not json")
        assert (await p.wait_for("error"))["data"]["code"] == "bad_message"
        pong = await p.command("ping")
        assert pong["type"] == "pong"


async def test_mode_change_and_random_mode_start(gclient: AsyncClient, content: dict) -> None:
    code, players = await _room_with(gclient, 2, None)
    async with contextlib.AsyncExitStack() as stack:
        sockets = []
        for uid, headers in players:
            p = WsPlayer(await stack.enter_async_context(connect(gclient, code, headers)), uid)
            await p.recv()
            sockets.append(p)
        host = sockets[0]
        bad = await host.command("room.update_settings", modeId=999999)
        assert bad["data"]["code"] == "mode_unavailable"
        ok = await host.command(
            "room.update_settings", modeId=content["hot_seat"], settings={"roundsCount": 2}
        )
        assert ok["type"] == "ack"
        s = await sockets[1].wait_for("room.settings")
        assert s["data"]["mode"]["kind"] == "hot_seat"
        await host.command("game.start")
        turn = await sockets[1].wait_for("hotseat.turn")
        assert turn["data"]["roundsCount"] == 2
        # a client cannot inject a mode object directly
        await host.command("game.end")
        await host.command("room.update_settings", mode={"id": 1, "kind": "evil"}, modeId=None)
        await host.send("room.resync")
        resync = await host.wait_for("room.snapshot")
        assert resync["data"]["mode"] is None
        # random mode: 2 connected players → one of the 2-player kinds with content
        await host.command("game.start")
        started = await sockets[1].wait_for("game.started")
        assert started["data"]["kind"] in ("wheel", "bomb", "hot_seat")
        assert json.dumps(started)  # serializable


async def test_head_healthz_for_curl_i(gclient: AsyncClient) -> None:
    for path in ("/healthz", "/health"):
        assert (await gclient.head(path)).status_code == 200
    assert (await gclient.get("/healthz")).json() == {"status": "ok"}
