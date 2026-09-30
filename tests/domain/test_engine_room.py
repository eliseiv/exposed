"""Room lifecycle: join, presence + reconnect grace, host transfer, kick, settings, start."""

from __future__ import annotations

import pytest

from app.domain.game.core import GRACE_TIMER, GameError
from app.domain.game.engine import room_view
from tests.domain.helpers import content, make_sim, mode, start


def test_join_emits_event_and_respects_capacity() -> None:
    sim = make_sim(2)
    assert sim.types().count("player.joined") == 1
    sim.room.max_players = 2
    with pytest.raises(GameError) as err:
        sim.run("room.join", "u9", nickname="late")
    assert err.value.code == "room_full"


def test_rejoin_updates_profile_without_duplicate() -> None:
    sim = make_sim(2)
    sim.run("room.join", "u2", nickname="Renamed")
    assert len(sim.room.players) == 2
    assert sim.room.player("u2").nickname == "Renamed"  # type: ignore[union-attr]


def test_disconnect_then_reconnect_within_grace_keeps_the_seat() -> None:
    sim = make_sim(3)
    epoch = sim.room.player("u2").conn_epoch  # type: ignore[union-attr]
    sim.run("presence.disconnect", "u2", conn_epoch=epoch)
    assert sim.last_event("player.disconnected").data["userId"] == "u2"
    sim.advance(10_000)
    sim.run("presence.connect", "u2")
    sim.advance(30_000)  # the old grace timer fires but is stale
    assert sim.room.player("u2") is not None
    assert sim.room.player("u2").connected  # type: ignore[union-attr]


def test_grace_expiry_in_lobby_removes_player() -> None:
    sim = make_sim(3)
    sim.run("presence.disconnect", "u3", conn_epoch=sim.room.player("u3").conn_epoch)  # type: ignore[union-attr]
    assert any(t.kind == GRACE_TIMER for t in sim.timers)
    sim.advance(30_000)
    assert sim.room.player("u3") is None
    assert sim.last_event("player.left").data == {"userId": "u3", "reason": "timeout"}


def test_stale_disconnect_of_replaced_connection_is_ignored() -> None:
    sim = make_sim(2)
    old_epoch = sim.room.player("u2").conn_epoch  # type: ignore[union-attr]
    sim.run("presence.connect", "u2")  # a new connection replaced the old one
    sim.run("presence.disconnect", "u2", conn_epoch=old_epoch)
    assert sim.room.player("u2").connected  # type: ignore[union-attr]


def test_host_leaving_transfers_host_to_earliest_connected() -> None:
    sim = make_sim(3)
    sim.run("room.leave", "u1")
    assert sim.room.host_id == "u2"
    assert sim.last_event("host.changed").data["reason"] == "host_left"


def test_last_player_leaving_closes_room() -> None:
    sim = make_sim(1)
    sim.run("room.leave", "u1")
    assert sim.room.closed


def test_kick_bans_rejoin_and_is_host_only() -> None:
    sim = make_sim(3)
    with pytest.raises(GameError) as err:
        sim.run("room.kick", "u2", userId="u3")
    assert err.value.code == "not_host"
    sim.run("room.kick", "u1", userId="u3")
    assert sim.room.player("u3") is None
    with pytest.raises(GameError) as err:
        sim.run("room.join", "u3", nickname="again")
    assert err.value.code == "kicked"


def test_update_settings_validates_against_mode() -> None:
    sim = make_sim(3)
    m = mode("bomb").model_dump()
    sim.run("room.update_settings", "u1", mode=m, categories=["spicy", "friendly"])
    assert sim.room.categories == ["friendly", "spicy"]
    with pytest.raises(GameError) as err:
        sim.run("room.update_settings", "u1", settings={"fuseMinSec": 100, "fuseMaxSec": 10})
    assert err.value.code == "invalid_settings"
    with pytest.raises(GameError):
        sim.run("room.update_settings", "u1", categories=["unknown"])


def test_start_requires_enough_connected_players() -> None:
    sim = make_sim(2)
    with pytest.raises(GameError) as err:
        sim.run("game.start", "u1", content=content("question_list"))
    assert err.value.code == "not_enough_players"


def test_join_during_game_rejected() -> None:
    sim = make_sim(3)
    start(sim, "question_list")
    with pytest.raises(GameError) as err:
        sim.run("room.join", "u9", nickname="late")
    assert err.value.code == "game_in_progress"


def test_player_dropping_below_minimum_finishes_game_and_returns_to_lobby() -> None:
    sim = make_sim(3)
    start(sim, "question_list")
    sim.run("room.leave", "u3")
    finished = sim.last_event("game.finished")
    assert finished.data["reason"] == "not_enough_players"
    assert sim.room.status == "lobby"
    assert sim.room.game is None
    assert [p.user_id for p in sim.room.players] == ["u1", "u2"]


def test_host_can_end_game() -> None:
    sim = make_sim(3)
    start(sim, "hot_seat", card_type="question")
    sim.run("game.end", "u1")
    assert sim.last_event("game.finished").data["reason"] == "host_ended"
    assert sim.last is not None and sim.last.archive is not None
    assert sim.last.archive["mode_kind"] == "hot_seat"


def test_room_view_is_personal_and_serializable() -> None:
    sim = make_sim(3)
    start(sim, "question_list")
    view = room_view(sim.room, "u2")
    assert view["status"] == "playing"
    assert view["game"]["kind"] == "question_list"
    assert view["game"]["phase"] == "voting"
    assert len(view["players"]) == 3
