"""Game rules of every mode, driven through the pure engine with a fake clock and seeded rng."""

from __future__ import annotations

import pytest

from app.domain.game.core import GameError
from app.domain.game.engine import room_view
from app.domain.game.state import ImpostorGame
from app.domain.game.voting import leaders, percentages, resolve_tie, tally
from tests.domain.helpers import make_sim, start


# ---- voting ---------------------------------------------------------------------------------
def test_percentages_sum_to_100() -> None:
    assert sum(percentages({"a": 1, "b": 1, "c": 1}).values()) == 100
    assert percentages({"a": 0, "b": 0}) == {"a": 0, "b": 0}


def test_tally_leaders_and_ties() -> None:
    import random

    counts = tally({"x": "a", "y": "b", "z": "a", "w": "zzz"}, ["a", "b", "c"])
    assert counts == {"a": 2, "b": 1, "c": 0}
    assert leaders(counts) == ["a"]
    assert leaders({"a": 0}) == []
    assert resolve_tie(["a", "b"], "all", random.Random(1)) == ["a", "b"]
    assert len(resolve_tie(["a", "b"], "random", random.Random(1))) == 1


# ---- question list --------------------------------------------------------------------------
def test_question_list_round_closes_when_everyone_voted() -> None:
    sim = make_sim(3)
    start(sim, "question_list", n=2)
    started = sim.last_event("round.started").data
    assert started["card"]["type"] == "pick_player"
    for uid in sim.ids:
        sim.run("vote.cast", uid, choice="u2")
    results = sim.last_event("round.results").data
    assert results["targets"][0]["userId"] == "u2"
    assert results["targets"][0]["nickname"] == "P2"
    assert results["percentages"]["u2"] == 100
    assert results["votes"] == {"u1": "u2", "u2": "u2", "u3": "u2"}
    sim.run("game.next", sim.host)
    assert sim.last_event("round.started").data["round"] == 2


def test_question_list_timer_closes_round_and_game_finishes_after_deck() -> None:
    sim = make_sim(3)
    start(sim, "question_list", n=1)
    sim.run("vote.cast", "u1", choice="u3")
    sim.advance(20_000)
    assert sim.last_event("round.results").data["totalVotes"] == 1
    sim.run("game.next", sim.host)
    assert sim.last_event("game.finished").data["reason"] == "completed"
    assert sim.room.status == "lobby"


def test_question_list_tie_all_and_scoring() -> None:
    sim = make_sim(4)
    start(sim, "question_list", n=3, card_type="dare", tiePolicy="all", scoring=True)
    sim.run("vote.cast", "u1", choice="u2")
    sim.run("vote.cast", "u2", choice="u3")
    sim.run("vote.cast", "u3", choice="u2")
    sim.run("vote.cast", "u4", choice="u3")
    res = sim.last_event("round.results").data
    assert res["tie"] is True
    assert {t["userId"] for t in res["targets"]} == {"u2", "u3"}
    assert res["points"] == {"u2": 1, "u3": 1}
    dare = sim.last_event("dare.assigned").data
    assert {t["userId"] for t in dare["targets"]} == {"u2", "u3"}


def test_question_list_yes_no_anonymous_hides_votes() -> None:
    sim = make_sim(3)
    start(sim, "question_list", n=1, card_type="yes_no")
    sim.room.game.current.card.is_anonymous = True  # type: ignore[union-attr]
    for uid, choice in (("u1", "yes"), ("u2", "yes"), ("u3", "no")):
        sim.run("vote.cast", uid, choice=choice)
    res = sim.last_event("round.results").data
    assert res["votes"] is None
    assert res["percentages"] == {"yes": 67, "no": 33}
    assert res["majority"] == "yes"


def test_question_list_player_placeholder_substituted() -> None:
    sim = make_sim(3)
    start(sim, "question_list", n=1, card_type="yes_no")
    game = sim.room.game
    assert game is not None
    card = game.current.card  # type: ignore[union-attr]
    card.text = "Did {player} jump?"
    sim.run("game.end", sim.host)
    # restart with a placeholder card
    from tests.domain.helpers import content

    c = content("question_list", card_type="yes_no", n=1)
    c["cards"][0]["text"] = "Did {player} jump?"
    sim.run("game.start", sim.host, content=c)
    card_view = sim.last_event("round.started").data
    assert "{player}" not in card_view["card"]["text"]
    assert card_view["subject"]["nickname"] in card_view["card"]["text"]


def test_invalid_vote_rejected() -> None:
    sim = make_sim(3)
    start(sim, "question_list", n=1, card_type="duel")
    candidates = [o["userId"] for o in sim.last_event("round.started").data["options"]]
    outsider = next(u for u in sim.ids if u not in candidates)
    with pytest.raises(GameError) as err:
        sim.run("vote.cast", "u1", choice=outsider)
    assert err.value.code == "invalid_vote"


def test_disconnected_voter_does_not_block_round() -> None:
    sim = make_sim(3)
    start(sim, "question_list", n=2)
    sim.run("vote.cast", "u1", choice="u2")
    sim.run("vote.cast", "u2", choice="u1")
    epoch = sim.room.player("u3").conn_epoch  # type: ignore[union-attr]
    sim.run("presence.disconnect", "u3", conn_epoch=epoch)
    assert sim.room.game.phase == "results"  # type: ignore[union-attr]


# ---- hot seat -------------------------------------------------------------------------------
def test_hot_seat_bag_gives_everyone_a_turn_and_ends_after_rounds() -> None:
    sim = make_sim(3)
    start(sim, "hot_seat", card_type="question", roundsCount=6)
    seen: list[str] = []
    for _ in range(6):
        turn = sim.last_event("hotseat.turn").data
        seen.append(turn["player"]["userId"])
        sim.run("hotseat.done", turn["player"]["userId"])
    assert sorted(seen[:3]) == ["u1", "u2", "u3"]
    assert sorted(seen[3:]) == ["u1", "u2", "u3"]
    assert sim.last_event("game.finished").data["leaderboard"] == []


def test_hot_seat_only_player_or_host_can_finish_turn() -> None:
    sim = make_sim(3)
    start(sim, "hot_seat", card_type="question")
    player = sim.last_event("hotseat.turn").data["player"]["userId"]
    other = next(u for u in sim.ids if u not in (player, sim.host))
    with pytest.raises(GameError):
        sim.run("hotseat.done", other)


# ---- wheel ----------------------------------------------------------------------------------
def test_wheel_collect_spin_until_empty() -> None:
    sim = make_sim(2)
    start(sim, "wheel")
    for uid in sim.ids:
        sim.run("wheel.submit", uid, question=f"q-{uid}", dare=f"d-{uid}", gossip=f"g-{uid}")
    assert sim.last_event("wheel.ready").data["remaining"] == {
        "question": 2,
        "dare": 2,
        "gossip": 2,
    }
    texts = set()
    for _ in range(6):
        sim.run("wheel.spin", sim.host)
        spun = sim.last_event("wheel.spun").data
        assert spun["segments"][spun["segmentIndex"]] == spun["category"]
        texts.add(spun["text"])
        with pytest.raises(GameError):  # cannot spin again mid-spin
            sim.run("wheel.spin", sim.host)
        sim.advance(spun["durationMs"])
    assert len(texts) == 6
    sim.run("wheel.spin", sim.host)
    assert sim.last_event("game.finished").data["reason"] == "completed"


def test_wheel_only_host_spins() -> None:
    sim = make_sim(2)
    start(sim, "wheel")
    sim.run("wheel.submit", "u2", question="q", dare="d", gossip="g")
    with pytest.raises(GameError) as err:
        sim.run("wheel.spin", "u2")
    assert err.value.code == "not_host"


# ---- bomb -----------------------------------------------------------------------------------
def test_bomb_pass_return_and_fuse_explosion() -> None:
    sim = make_sim(3)
    start(sim, "bomb", card_type="question", roundsCount=1, returnWindowSec=5)
    holder = sim.last_event("bomb.round_started").data["holder"]["userId"]
    assert sim.last_event("bomb.round_started").data["endsAt"] is None  # hidden fuse
    with pytest.raises(GameError):
        sim.run("bomb.pass", next(u for u in sim.ids if u != holder))
    sim.run("bomb.pass", holder)
    passed = sim.last_event("bomb.passed").data
    new_holder = passed["to"]["userId"]
    assert new_holder != holder
    sim.run("bomb.return", new_holder)
    assert sim.room.game.holder == holder  # type: ignore[union-attr]
    with pytest.raises(GameError) as err:  # only once
        sim.run("bomb.return", holder)
    assert err.value.code == "return_not_allowed"
    sim.advance(10 * 60_000)
    exploded = sim.last_event("bomb.exploded").data
    assert exploded["loser"]["userId"] == holder
    assert exploded["reason"] == "fuse"
    sim.run("game.next", sim.host)
    board = sim.last_event("game.finished").data["leaderboard"]
    assert board[-1]["userId"] == holder and board[-1]["score"] == 1


def test_bomb_return_window_expires() -> None:
    sim = make_sim(3)
    start(sim, "bomb", card_type="question", fuseMinSec=100, fuseMaxSec=100)
    holder = sim.room.game.holder  # type: ignore[union-attr]
    sim.run("bomb.pass", holder)
    new_holder = sim.room.game.holder  # type: ignore[union-attr]
    sim.now += 6_000
    with pytest.raises(GameError):
        sim.run("bomb.return", new_holder)


def test_bomb_skip_explodes_with_chance_one() -> None:
    sim = make_sim(2)
    start(sim, "bomb", card_type="question", skipExplodeChance=1.0)
    sim.run("bomb.skip", sim.room.game.holder)  # type: ignore[union-attr]
    assert sim.last_event("bomb.exploded").data["reason"] == "skip"


def test_bomb_skip_with_chance_zero_changes_question() -> None:
    sim = make_sim(2)
    start(sim, "bomb", card_type="question", skipExplodeChance=0.0)
    sim.run("bomb.skip", sim.room.game.holder)  # type: ignore[union-attr]
    assert "bomb.question_changed" in sim.types()


# ---- impostor -------------------------------------------------------------------------------
def _impostor_game(sim) -> ImpostorGame:  # type: ignore[no-untyped-def]
    game = sim.room.game
    assert isinstance(game, ImpostorGame)
    return game


def test_impostor_roles_are_private() -> None:
    sim = make_sim(4)
    start(sim, "impostor", hintForImpostor=True)
    game = _impostor_game(sim)
    roles = [e for e in sim.events if e.type == "impostor.role"]
    assert len(roles) == 4 and all(e.to and len(e.to) == 1 for e in roles)
    imp = game.impostors[0]
    imp_role = next(e for e in roles if e.to == [imp]).data
    assert imp_role["role"] == "impostor" and imp_role["word"] is None and imp_role["hint"]
    civ = next(u for u in sim.ids if u != imp)
    assert room_view(sim.room, civ)["game"]["me"]["word"] == game.word.word  # type: ignore[union-attr]
    assert room_view(sim.room, imp)["game"]["me"]["word"] is None


def test_impostor_found_civilians_win() -> None:
    sim = make_sim(4)
    start(sim, "impostor", gamesCount=1)
    game = _impostor_game(sim)
    for _ in range(4):
        sim.run("impostor.done_speaking", game.speaking_order[game.speaker_idx])
    assert game.phase == "voting"
    imp = game.impostors[0]
    for uid in sim.ids:
        target = imp if uid != imp else next(u for u in sim.ids if u != imp)
        sim.run("impostor.vote", uid, target=target)
    result = sim.last_event("impostor.vote_result").data
    assert result["eliminated"]["userId"] == imp and result["wasImpostor"] is True
    over = sim.last_event("impostor.game_over").data
    assert over["winner"] == "civilians"
    sim.run("game.next", sim.host)
    board = sim.last_event("game.finished").data["leaderboard"]
    assert board[0]["score"] == 3  # +1 correct vote, +2 win
    assert next(r for r in board if r["userId"] == imp)["score"] == 0


def test_impostor_survives_max_rounds_wins() -> None:
    sim = make_sim(5)
    start(sim, "impostor", gamesCount=1, maxRounds=1)
    game = _impostor_game(sim)
    sim.advance(5 * 30_000)  # everyone's speaking time runs out
    assert game.phase == "voting"
    sim.advance(30_000)  # nobody voted: tie → nobody eliminated
    assert sim.last_event("impostor.vote_result").data["eliminated"] is None
    assert sim.last_event("impostor.game_over").data["winner"] == "impostors"


def test_impostor_count_must_be_below_half() -> None:
    sim = make_sim(4)
    with pytest.raises(GameError) as err:
        start(sim, "impostor", impostorCount=2)
    assert err.value.code == "invalid_settings"


# ---- fill the blank -------------------------------------------------------------------------
def test_fill_blank_options_are_private_and_disjoint() -> None:
    sim = make_sim(4)
    start(sim, "fill_blank", card_type="blank_prompt", n=2)
    dealt = [e for e in sim.events if e.type == "blank.options"]
    assert len(dealt) == 3  # everybody but the judge
    ids = [o["id"] for e in dealt for o in e.data["options"]]
    assert len(ids) == len(set(ids)) == 15


def test_fill_blank_full_round_with_judge_pick() -> None:
    sim = make_sim(3)
    start(sim, "fill_blank", card_type="blank_prompt", n=1)
    game = sim.room.game
    judge = game.judge  # type: ignore[union-attr]
    for e in [e for e in sim.events if e.type == "blank.options"]:
        sim.run("blank.submit", e.to[0], optionId=e.data["options"][0]["id"])  # type: ignore[index]
    judging = sim.last_event("blank.judging").data
    assert all("author" not in a for a in judging["answers"])
    with pytest.raises(GameError):
        sim.run("blank.pick", next(u for u in sim.ids if u != judge), answerId="a1")
    sim.run("blank.pick", judge, answerId=judging["answers"][0]["answerId"])
    res = sim.last_event("round.results").data
    assert res["winner"]["userId"] != judge
    assert all(a["author"] for a in res["answers"])
    sim.run("game.next", sim.host)
    finished = sim.last_event("game.finished").data
    assert finished["leaderboard"][0]["score"] == 1


def test_fill_blank_free_text_and_judge_timeout_random_pick() -> None:
    sim = make_sim(3)
    start(sim, "fill_blank", card_type="blank_prompt", n=2, answerMode="free")
    judge = sim.room.game.judge  # type: ignore[union-attr]
    for uid in sim.ids:
        if uid != judge:
            sim.run("blank.submit", uid, text=f"text from {uid}")
    sim.advance(30_000)
    res = sim.last_event("round.results").data
    assert res["randomPick"] is True
    sim.run("game.next", sim.host)
    assert sim.room.game.judge != judge  # type: ignore[union-attr]  # judge rotates


# ---- players dropping out mid-game ----------------------------------------------------------
def test_impostor_leaving_mid_game_ends_it_for_civilians() -> None:
    sim = make_sim(5)
    start(sim, "impostor", gamesCount=1)
    game = _impostor_game(sim)
    imp = game.impostors[0]
    sim.run("room.leave", imp)
    assert sim.last_event("impostor.game_over").data["winner"] == "civilians"


def test_current_speaker_leaving_passes_the_turn() -> None:
    sim = make_sim(5)
    start(sim, "impostor", gamesCount=1)
    game = _impostor_game(sim)
    speaker = game.speaking_order[game.speaker_idx]
    if speaker in game.impostors or speaker == sim.host:
        speaker = next(u for u in game.speaking_order if u not in game.impostors and u != sim.host)
        while game.speaking_order[game.speaker_idx] != speaker:
            sim.run("impostor.done_speaking", sim.host)
    sim.run("room.leave", speaker)
    assert game.phase == "speaking"
    assert game.speaking_order[game.speaker_idx] != speaker
    assert speaker not in game.alive


def test_bomb_holder_leaving_passes_the_bomb() -> None:
    sim = make_sim(3)
    start(sim, "bomb", card_type="question")
    holder = sim.room.game.holder  # type: ignore[union-attr]
    sim.run("room.leave", holder)
    passed = sim.last_event("bomb.passed").data
    assert passed["reason"] == "left" and passed["to"]["userId"] != holder


def test_judge_leaving_during_judging_picks_randomly() -> None:
    sim = make_sim(4)
    start(sim, "fill_blank", card_type="blank_prompt", n=2, answerMode="free")
    judge = sim.room.game.judge  # type: ignore[union-attr]
    for uid in sim.ids:
        if uid != judge:
            sim.run("blank.submit", uid, text="x")
    assert sim.room.game.phase == "judging"  # type: ignore[union-attr]
    sim.run("room.leave", judge)
    assert sim.last_event("round.results").data["randomPick"] is True


def test_hot_seat_player_leaving_does_not_burn_a_round() -> None:
    sim = make_sim(3)
    start(sim, "hot_seat", card_type="question", roundsCount=5)
    player = sim.room.game.player  # type: ignore[union-attr]
    sim.run("room.leave", player)
    game = sim.room.game
    assert game is not None and game.round == 1 and game.player != player


def test_stale_phase_timer_is_ignored() -> None:
    sim = make_sim(3)
    start(sim, "question_list", n=3)
    old_phase = sim.room.game.phase_id  # type: ignore[union-attr]
    for uid in sim.ids:
        sim.run("vote.cast", uid, choice="u1")
    sim.run("timer", None, kind="phase", token=str(old_phase))  # the voting timer, now stale
    assert sim.room.game.phase == "results"  # type: ignore[union-attr]
    assert sim.types().count("round.results") == 1
