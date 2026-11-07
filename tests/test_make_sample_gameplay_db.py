"""scripts/make_sample_gameplay_db.py: the made-up gameplay log the ML pipeline is tested on."""
from __future__ import annotations

import hashlib
import sqlite3
import struct
from collections import Counter, defaultdict

import pytest

from scripts import make_sample_gameplay_db as sample
from server.gameplay_log import CLOSED, DECIDED, NO_DECISION, NO_SUBMISSIONS, SCHEMA, unpack_ids


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("sample") / "gameplay.db"
    truth = sample.generate(path, games=60, seed=1)
    conn = sqlite3.connect(path)
    yield conn, truth
    conn.close()


def schema_of(conn: sqlite3.Connection) -> list[tuple]:
    return sorted(conn.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))


def file_hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_schema_is_exactly_the_games(db, tmp_path):
    conn, _ = db
    reference = sqlite3.connect(tmp_path / "reference.db")
    reference.executescript(SCHEMA)
    assert schema_of(conn) == schema_of(reference)


def test_same_seed_same_file_other_seed_other_file(tmp_path):
    for name, seed in (("a", 3), ("b", 3), ("c", 4)):
        sample.generate(tmp_path / f"{name}.db", games=10, seed=seed)
    assert file_hash(tmp_path / "a.db") == file_hash(tmp_path / "b.db")
    assert file_hash(tmp_path / "a.db") != file_hash(tmp_path / "c.db")


def test_every_case_the_real_log_has_is_covered(db):
    conn, _ = db
    q = lambda sql: {row[0] for row in conn.execute(sql)}  # noqa: E731
    assert q("SELECT outcome FROM round") == {DECIDED, NO_SUBMISSIONS, NO_DECISION, CLOSED}
    assert q("SELECT vote_mode FROM game") == {0, 1}
    assert q("SELECT self_vote FROM game") == {0, 1}
    assert q("SELECT ct.pick FROM round r JOIN card_text ct ON ct.text_id = r.black") == {1, 2, 3}
    assert 1 in q("SELECT COUNT(*) FROM game_player GROUP BY game_id"), "solo games"
    assert q("SELECT COUNT(*) FROM game_player WHERE model_id IS NOT NULL") != {0}, "model players"
    assert q("SELECT COUNT(*) FROM decision WHERE picks IS NULL") != {0}, "players who didn't submit"
    removed_sizes = {len(unpack_ids(r)) for (r,) in conn.execute("SELECT removed FROM hand_change")}
    assert removed_sizes == {1, sample.HAND_SIZE}, "swaps and new hands"
    assert q("SELECT COUNT(*) - COUNT(DISTINCT card_id) FROM card_text") == {1}, "one reworded card"


def test_cards(db):
    conn, truth = db
    for text_id, card_id, black, pick, text in conn.execute("SELECT * FROM card_text"):
        assert card_id > sample.CARD_ID_BASE
        assert text
        if black:
            assert pick == text.count("____") == truth.pick[text_id]
        else:
            assert pick == 0 and text_id in truth.quality
        # The topic word is in the text, so embeddings can see the topic match.
        assert truth.topic[text_id] in text.lower()


def test_games_are_in_date_order_within_the_requested_span(db):
    conn, _ = db
    days = [day for (day,) in conn.execute("SELECT day FROM game ORDER BY game_id")]
    assert days == sorted(days)
    assert sample.FIRST_DAY <= days[0] and days[-1] < sample.FIRST_DAY + 14


def test_every_decision_follows_the_game_rules(db):
    conn, _ = db
    black_of = dict(conn.execute("SELECT text_id, black FROM card_text"))
    pick_of = dict(conn.execute("SELECT text_id, pick FROM card_text"))
    rounds = {(g, r): black for g, r, black in conn.execute("SELECT game_id, round_no, black FROM round")}
    players = dict(conn.execute("SELECT game_id, COUNT(*) FROM game_player GROUP BY game_id"))
    rows = conn.execute("SELECT game_id, round_no, seat, hand, picks FROM decision").fetchall()
    per_round = Counter((g, r) for g, r, *_ in rows)

    for game_id, round_no, seat, hand_blob, picks_blob in rows:
        hand = unpack_ids(hand_blob)
        assert len(hand) == len(set(hand)) == sample.HAND_SIZE
        assert all(black_of[c] == 0 for c in hand)
        assert 0 <= seat < players[game_id]
        if picks_blob is not None:
            picks = unpack_ids(picks_blob)
            assert set(picks) <= set(hand)
            assert len(picks) == len(set(picks)) == pick_of[rounds[game_id, round_no]]
    # One decision row per player per round, submitted or not.
    assert all(per_round[key] == players[key[0]] for key in rounds)
    assert all(black_of[black] == 1 for black in rounds.values())


def test_no_card_is_in_two_hands_at_once(db):
    conn, _ = db
    hands = defaultdict(list)
    for game_id, round_no, hand in conn.execute("SELECT game_id, round_no, hand FROM decision"):
        hands[game_id, round_no].extend(unpack_ids(hand))
    assert all(len(cards) == len(set(cards)) for cards in hands.values())


def test_hand_changes_end_in_the_logged_hand(db):
    conn, _ = db
    final = {(g, r, s): set(unpack_ids(h)) for g, r, s, h in conn.execute("SELECT game_id, round_no, seat, hand FROM decision")}
    changes = defaultdict(list)
    for g, r, s, seq, removed, added in conn.execute("SELECT * FROM hand_change ORDER BY game_id, round_no, seat, seq"):
        changes[g, r, s].append((seq, unpack_ids(removed), unpack_ids(added)))
    for key, steps in changes.items():
        assert [seq for seq, _, _ in steps] == list(range(len(steps)))
        _, removed, added = steps[-1]
        assert len(removed) == len(added)
        assert set(added) <= final[key]
        assert not set(removed) & final[key]


def test_rounds_votes_and_czars(db):
    conn, _ = db
    games = {g: (mode, self_vote) for g, mode, self_vote in conn.execute("SELECT game_id, vote_mode, self_vote FROM game")}
    players = dict(conn.execute("SELECT game_id, COUNT(*) FROM game_player GROUP BY game_id"))
    submitted = defaultdict(set)
    for g, r, s in conn.execute("SELECT game_id, round_no, seat FROM decision WHERE picks IS NOT NULL"):
        submitted[g, r].add(s)
    votes = defaultdict(list)
    for g, r, voter, target in conn.execute("SELECT * FROM vote"):
        votes[g, r].append((voter, target))

    for g, r, t, _black, czar, outcome, eligible, shown in conn.execute("SELECT * FROM round"):
        vote_mode, self_vote = games[g]
        assert t >= 0
        if vote_mode:
            assert czar is None and eligible == players[g]
        else:
            assert czar == (r - 1) % players[g] and eligible == 1
            assert all(voter == czar for voter, _ in votes[g, r])
        if outcome in (NO_SUBMISSIONS, CLOSED):
            assert shown is None and not votes[g, r]
        if outcome == NO_SUBMISSIONS:
            assert not submitted[g, r]
        if outcome in (DECIDED, NO_DECISION):
            shown_seats = unpack_ids(shown)
            assert sorted(shown_seats) == sorted(submitted[g, r]) and shown_seats
            assert bool(votes[g, r]) == (outcome == DECIDED)
        for voter, target in votes[g, r]:
            assert target in submitted[g, r]
            if voter == target:
                assert self_vote or players[g] == 1


def test_games_end_after_a_closed_round(db):
    conn, _ = db
    last = dict(conn.execute("SELECT game_id, MAX(round_no) FROM round GROUP BY game_id"))
    for g, r in conn.execute("SELECT game_id, round_no FROM round WHERE outcome = ?", (CLOSED,)):
        assert r == last[g]


def test_model_players_have_probabilities(db):
    conn, _ = db
    model_seats = set(conn.execute("SELECT game_id, seat FROM game_player WHERE model_id IS NOT NULL"))
    assert model_seats
    for g, _r, s, hand, picks, p_chosen, probs in conn.execute("SELECT * FROM decision"):
        if (g, s) in model_seats and picks is not None:
            values = struct.unpack(f"<{len(probs) // 2}e", probs)
            assert len(values) == len(unpack_ids(hand))
            assert sum(values) == pytest.approx(1, abs=0.01) and 0 < p_chosen <= 1
        else:
            assert p_chosen is None and probs is None
    assert conn.execute("SELECT name, version FROM model").fetchall() == [sample.MODEL]


def test_the_planted_signal_is_learnable(db):
    """Humans prefer high-quality cards and cards on the black card's topic, so a model can beat random
    picks and the popularity baseline has something to find."""
    conn, truth = db
    model_seats = set(conn.execute("SELECT game_id, seat FROM game_player WHERE model_id IS NOT NULL"))
    rounds = {(g, r): black for g, r, black in conn.execute("SELECT game_id, round_no, black FROM round")}
    picked_q, unpicked_q = [], []
    topic_hits = topic_chance = 0.0
    for g, r, s, hand, picks in conn.execute("SELECT game_id, round_no, seat, hand, picks FROM decision "
                                             "WHERE picks IS NOT NULL"):
        if (g, s) in model_seats:
            continue
        hand, picks = unpack_ids(hand), set(unpack_ids(picks))
        topic = truth.topic[rounds[g, r]]
        picked_q += [truth.quality[c] for c in picks]
        unpicked_q += [truth.quality[c] for c in hand if c not in picks]
        topic_hits += sum(truth.topic[c] == topic for c in picks) / len(picks)
        topic_chance += sum(truth.topic[c] == topic for c in hand) / len(hand)
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    assert mean(picked_q) > mean(unpicked_q) + 0.5
    assert topic_hits > 2 * topic_chance


def test_generate_rejects_empty_runs(tmp_path):
    with pytest.raises(ValueError):
        sample.generate(tmp_path / "x.db", games=0)


def test_main_writes_and_refuses_to_overwrite(tmp_path, capsys):
    out = tmp_path / "sub" / "gameplay.db"
    assert sample.main(["--out", str(out), "--games", "3", "--seed", "5"]) == 0
    first = file_hash(out)
    assert "Wrote 3 sample games" in capsys.readouterr().out

    assert sample.main(["--out", str(out), "--games", "3", "--seed", "6"]) == 1
    assert file_hash(out) == first
    assert "--force" in capsys.readouterr().err

    assert sample.main(["--out", str(out), "--games", "3", "--seed", "6", "--force"]) == 0
    assert file_hash(out) != first
    assert list(out.parent.iterdir()) == [out], "no temporary files left behind"
