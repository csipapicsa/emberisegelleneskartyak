"""ml/validate.py: the game's rules checked on a snapshot. Each rule gets one deliberate break."""
from __future__ import annotations

import copy
import json

import pytest

from ml import export, validate
from scripts import make_sample_gameplay_db as sample
from server.gameplay_log import CLOSED, DECIDED, NO_DECISION


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "gameplay.db"
    sample.generate(db, games=30, seed=3)
    folder, _ = export.export(db, tmp_path_factory.mktemp("snapshots"))
    return folder


@pytest.fixture(scope="module")
def tables(snapshot):
    return export.read_snapshot(snapshot)[1]


def run(tables) -> dict[str, validate.CheckResult]:
    return {r.name: r for r in validate.validate(tables, first_day=sample.FIRST_DAY)}


def test_sample_data_passes_every_check(tables):
    results = run(tables)
    assert [name for name, r in results.items() if not r.ok] == []
    assert len(results) == len(validate.CHECKS) >= 13


def _first(rows, **where):
    return next(r for r in rows if all(r[k] == v if not callable(v) else v(r[k]) for k, v in where.items()))


def _decided_round(t, vote_mode: bool):
    games = {g["game_id"]: g for g in t["games"]}
    voted = {(v["game_id"], v["round_no"]) for v in t["votes"]}
    return next(r for r in t["rounds"] if r["outcome"] == DECIDED and games[r["game_id"]]["vote_mode"] == vote_mode
                and (r["game_id"], r["round_no"]) in voted)


def _submission(t):
    return _first(t["decisions"], picks=lambda p: p is not None, is_model=False)


def _black_card(t):
    return _first(t["card_texts"], black=True)["text_id"]


def break_card_refs(t):
    _submission(t)["hand"][0] = 999_999


def break_card_colors(t):
    d = _submission(t)
    d["hand"][d["hand"].index(next(c for c in d["hand"] if c not in d["picks"]))] = _black_card(t)


def break_hand_size(t):
    d = _submission(t)
    d["hand"].remove(next(c for c in d["hand"] if c not in d["picks"]))


def break_picks_in_hand(t):
    d = _submission(t)
    d["hand"].remove(d["picks"][0])
    d["hand"].append(_first(t["card_texts"], black=False, text_id=lambda c: c not in d["hand"])["text_id"])


def break_pick_count(t):
    d = _submission(t)
    d["picks"] = d["picks"] + [next(c for c in d["hand"] if c not in d["picks"])]


def break_rows_belong(t):
    t["votes"][0]["voter"] = 99


def break_rounds_in_game(t):
    games = {g["game_id"]: g for g in t["games"]}
    r = _first(t["rounds"], round_no=lambda n: n > 1)
    games[r["game_id"]]["rounds"] = r["round_no"] - 1


def break_outcome(t):
    r = _decided_round(t, vote_mode=True)
    t["votes"] = [v for v in t["votes"] if (v["game_id"], v["round_no"]) != (r["game_id"], r["round_no"])]


def break_shown(t):
    r = _first(t["rounds"], outcome=lambda o: o in (DECIDED, NO_DECISION), shown=lambda s: s and len(s) > 1)
    r["shown"] = r["shown"][1:]


def break_votes(t):
    r = _decided_round(t, vote_mode=True)
    submitted = {d["seat"] for d in t["decisions"]
                 if (d["game_id"], d["round_no"]) == (r["game_id"], r["round_no"]) and d["picks"] is not None}
    players = next(g["players"] for g in t["games"] if g["game_id"] == r["game_id"])
    idle = [s for s in range(players) if s not in submitted]
    v = _first(t["votes"], game_id=r["game_id"], round_no=r["round_no"])
    if idle:
        v["target"] = idle[0]
    else:  # everyone submitted: a self-vote where it isn't allowed breaks the same check
        next(g for g in t["games"] if g["game_id"] == r["game_id"])["self_vote"] = False
        v["target"] = v["voter"]


def break_czar(t):
    r = _decided_round(t, vote_mode=False)
    next(g for g in t["games"] if g["game_id"] == r["game_id"])["self_vote"] = False
    v = _first(t["votes"], game_id=r["game_id"], round_no=r["round_no"])
    v["target"] = r["czar"]


def break_hand_changes(t):
    t["hand_changes"][0]["seq"] = 5


def break_days(t):
    t["games"][0]["day"] = 0


BREAKS = {name: globals()[f"break_{name}"] for name, _, _ in validate.CHECKS}


@pytest.mark.parametrize("check", sorted(BREAKS))
def test_each_check_catches_its_break(tables, check):
    broken = copy.deepcopy(tables)
    BREAKS[check](broken)
    result = run(broken)[check]
    assert not result.ok
    assert result.examples and result.failures >= 1


def test_a_closed_round_must_be_the_games_last(tables):
    broken = copy.deepcopy(tables)
    r = _first(broken["rounds"], round_no=lambda n: n > 1)
    _first(broken["rounds"], game_id=r["game_id"], round_no=r["round_no"] - 1)["outcome"] = CLOSED
    assert not run(broken)["rounds_in_game"].ok


def test_examples_are_capped(tables):
    broken = copy.deepcopy(tables)
    for g in broken["games"]:
        g["day"] = 0
    result = run(broken)["days"]
    assert result.failures == len(broken["games"]) > validate.MAX_EXAMPLES
    assert len(result.examples) == validate.MAX_EXAMPLES


def test_validate_snapshot_writes_the_report(snapshot):
    results = validate.validate_snapshot(snapshot, first_day=sample.FIRST_DAY)
    report = json.loads((snapshot / validate.REPORT).read_text())
    assert report["ok"] is True and all(r.ok for r in results)
    assert report["content_hash"] == json.loads((snapshot / export.MANIFEST).read_text())["content_hash"]
    assert [c["name"] for c in report["checks"]] == [name for name, _, _ in validate.CHECKS]


def test_main_exit_codes(snapshot, tmp_path, capsys):
    assert validate.main(["--first-day", "2025-01-01", str(snapshot)]) == 0
    assert "checks passed" in capsys.readouterr().out
    # The default earliest day is when real logging began; the 2025 sample games fall before it.
    assert validate.main([str(snapshot)]) == 1
    out = capsys.readouterr().out
    assert "FAIL days" in out and "failed: days" in out
    assert validate.main([str(tmp_path / "missing")]) == 1
    assert "failed to run" in capsys.readouterr().err


# Real game situations the first weekend of data showed; each one is allowed by the game (server/game.py).

def _round_votes(t, r):
    return [v for v in t["votes"] if (v["game_id"], v["round_no"]) == (r["game_id"], r["round_no"])]


def test_no_decision_before_the_reveal_is_allowed(tables):
    # The czar didn't submit and was kicked: the round ends without a reveal, shown stays empty.
    t = copy.deepcopy(tables)
    r = _first(t["rounds"], outcome=NO_DECISION, shown=lambda s: s is not None)
    r["shown"] = None
    assert run(t)["shown"].ok


def test_more_votes_than_players_connected_at_the_end_is_allowed(tables):
    # A player votes, then disconnects before the tally; eligible counts who's connected at the end.
    t = copy.deepcopy(tables)
    r = _decided_round(t, vote_mode=True)
    r["eligible"] = len(_round_votes(t, r)) - 1
    assert run(t)["votes"].ok


def test_a_player_voting_twice_is_caught(tables):
    t = copy.deepcopy(tables)
    r = _decided_round(t, vote_mode=True)
    t["votes"].append(dict(_round_votes(t, r)[0]))
    assert not run(t)["votes"].ok


def test_the_last_player_left_may_vote_for_their_own_card(tables):
    t = copy.deepcopy(tables)
    r = _decided_round(t, vote_mode=True)
    next(g for g in t["games"] if g["game_id"] == r["game_id"])["self_vote"] = False
    submitted = {d["seat"] for d in t["decisions"]
                 if (d["game_id"], d["round_no"]) == (r["game_id"], r["round_no"]) and d["picks"] is not None}
    v = dict(_round_votes(t, r)[0], voter=min(submitted))
    t["votes"] = [x for x in t["votes"] if x not in _round_votes(t, r)] + [v]
    v["target"], r["eligible"] = v["voter"], 1
    assert run(t)["votes"].ok
    r["eligible"] = 2
    assert not run(t)["votes"].ok


def test_the_czar_may_pick_their_own_card_with_self_voting_on(tables):
    t = copy.deepcopy(tables)
    r = _decided_round(t, vote_mode=False)
    next(g for g in t["games"] if g["game_id"] == r["game_id"])["self_vote"] = True
    _round_votes(t, r)[0]["target"] = r["czar"]
    assert run(t)["czar"].ok


def _new_hand_change(t):
    """Replace one submission's hand changes with a single new hand; returns (change, hand)."""
    d = _submission(t)
    key = (d["game_id"], d["round_no"], d["seat"])
    t["hand_changes"] = [c for c in t["hand_changes"] if (c["game_id"], c["round_no"], c["seat"]) != key]
    others = [c["text_id"] for c in t["card_texts"] if not c["black"] and c["text_id"] not in d["hand"]]
    change = {"game_id": d["game_id"], "round_no": d["round_no"], "seat": d["seat"], "seq": 0,
              "removed": others[:10], "added": list(d["hand"])}
    t["hand_changes"].append(change)
    return change, d["hand"]


def test_a_new_hand_may_deal_back_cards_from_the_old_one(tables):
    # Game 205 (2026-11-13), 55 white cards: the deck ran out mid-deal and the reshuffled discard pile
    # held the hand being replaced, so the new hand repeated some old cards.
    t = copy.deepcopy(tables)
    change, hand = _new_hand_change(t)
    assert run(t)["hand_changes"].ok
    change["removed"][:3] = hand[:3]
    assert run(t)["hand_changes"].ok


def test_a_removed_card_still_in_the_hand_is_caught(tables):
    t = copy.deepcopy(tables)
    change, hand = _new_hand_change(t)
    change["removed"], change["added"] = [hand[0]], [hand[1]]  # a swap whose old card is still in the hand
    assert not run(t)["hand_changes"].ok
