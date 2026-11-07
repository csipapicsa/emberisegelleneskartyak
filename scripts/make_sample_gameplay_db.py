"""Build a gameplay.db with made-up games, for developing and testing the ML pipeline.

The real gameplay log (docs/ml/gameplay-logging.md) stays on the server; it is never committed and CI
never sees it. This script writes a stand-in with exactly the game's schema (it imports SCHEMA and the
ID encoding from server/gameplay_log.py), filled with simulated games on made-up cards, so the pipeline
code can be tested anywhere.

    python scripts/make_sample_gameplay_db.py                         # sample-data/gameplay.db
    python scripts/make_sample_gameplay_db.py --games 200 --seed 7 --out x.db
    python scripts/make_sample_gameplay_db.py --force                 # replace an existing file

The same seed always gives the same file. It refuses to overwrite an existing file without --force.

What the simulation covers, because the real log has all of it:
- czar and vote mode; in czar mode the czar plays a card too and can't pick their own (game.py),
  except in a solo game, where the czar's own card is the only one;
- solo games, players who don't submit, rounds where nobody submits (outcome 1), rounds with
  submissions but no vote (outcome 2), games closed mid-round (outcome 3);
- black cards with 1, 2 or 3 blanks; new hands before submitting (hand_change), rare single swaps;
- a reworded card (same card_id, new text_id);
- a few games with a model player (game_player.model_id, decision.p_chosen and probs), which training
  must leave out.

A learnable signal is planted, so a model trained on this data can beat random picks: every white card
has a hidden quality, and each card (black and white) has a topic whose word appears in its text.
Players pick and vote by quality, plus a bonus when the white card's topic matches the black card's,
plus noise. generate() returns these hidden values (SampleTruth) for tests.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.gameplay_log import CLOSED, DECIDED, NO_DECISION, NO_SUBMISSIONS, SCHEMA, pack_ids  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "sample-data" / "gameplay.db"
HAND_SIZE = 10
FIRST_DAY = 20760          # 2026-11-03, days since 1970-01-01, like game.day
CARD_ID_BASE = 90_000_000  # far above the real card IDs, so sample cards can't be mistaken for real ones
APP_VERSION = "sample"
MODEL = ("random-bot", "0")

# Topic word -> (black card templates, white card nouns). The topic word is in every card's text, so
# text embeddings can pick up the topic match.
TOPICS: dict[str, tuple[list[str], list[str]]] = {
    "macska": (["Mit csinál a macska éjjel? ____", "A macska titkos hobbija: ____"],
               ["egy kövér macska", "macskaszőr mindenhol", "a macska bosszúja", "macskakaja vacsorára"]),
    "főnök": (["A főnök szerint a siker kulcsa: ____", "Hétfő reggel a főnök ezzel fogad: ____"],
              ["a főnök új ötlete", "egy főnöki e-mail", "a főnök kutyája", "fizetésemelés helyett pizza a főnöktől"]),
    "anyós": (["Az anyósom kedvenc témája: ____", "Karácsonykor az anyós mindig hozza: ____"],
              ["az anyós rántottája", "anyósos vasárnap", "az anyós véleménye", "anyósülés a mennyben"]),
    "nyaralás": (["A nyaralás legemlékezetesebb pillanata: ____", "Idén nyaralás helyett: ____"],
                 ["napégés a nyaraláson", "nyaralás a Balatonon", "elveszett bőrönd a nyaralás végén",
                  "nyaraláskor is dolgozni"]),
    "adó": (["Az adóbevallásba ezt is beírtam: ____", "Az adóhivatal legújabb ötlete: ____"],
            ["adó a levegőre", "adóellenőr a spájzban", "egy adóslevél", "adókedvezmény macskáknak"]),
    "szomszéd": (["A szomszéd minden éjjel ezt csinálja: ____", "A szomszédtól kölcsönkértem: ____"],
                 ["a szomszéd fúrógépe", "szomszédi pletyka", "a szomszéd kakasa", "egy szomszédból lopott alma"]),
    "edzés": (["Az edzés után jutalmul: ____", "Az új edzéstervem lényege: ____"],
              ["edzés a kanapén", "egy edzésnapló üres lapjai", "edzésruha a szekrény mélyén", "fehérje edzés előtt"]),
    "randi": (["Az első randi legkínosabb része: ____", "Randi után ezt írtam neki: ____"],
              ["randi a nagymamával", "egy randiapp értesítése", "randin elfelejtett név", "vak randi sötétben"]),
}
ADJECTIVES = ["", "titokzatos ", "nagyon ", "kissé gyanús ", "teljesen ", "hivatalos ", "elátkozott "]
TWO_BLANKS = "Először ____, aztán ____ – ez a {topic} lényege."
THREE_BLANKS = "Recept a tökéletes {topic} témához: ____, ____ és ____."

# How often a black card with 1, 2 or 3 blanks comes up, roughly as in the real log (85% / 13% / under 1%).
PICK_WEIGHTS = {1: 1.0, 2: 0.3, 3: 0.1}
QUALITY_WEIGHT = 1.0
TOPIC_BONUS = 1.5
NOISE = 1.0  # scale of the Gumbel noise: higher means more random choices


@dataclass
class SampleTruth:
    """The hidden values behind the simulated choices, keyed by text_id."""
    quality: dict[int, float] = field(default_factory=dict)   # white cards
    topic: dict[int, str] = field(default_factory=dict)       # black and white cards
    pick: dict[int, int] = field(default_factory=dict)        # black cards: number of blanks


@dataclass
class _Cards:
    rows: list[tuple] = field(default_factory=list)  # card_text rows
    blacks: list[int] = field(default_factory=list)
    whites: list[int] = field(default_factory=list)
    truth: SampleTruth = field(default_factory=SampleTruth)


def _make_cards(rng: random.Random) -> _Cards:
    cards = _Cards()
    card_id = CARD_ID_BASE

    def add(text: str, black: bool, topic: str, pick: int = 0, same_card: bool = False) -> int:
        nonlocal card_id
        if not same_card:
            card_id += 1
        text_id = len(cards.rows) + 1
        cards.rows.append((text_id, card_id, int(black), pick, text))
        cards.truth.topic[text_id] = topic
        (cards.blacks if black else cards.whites).append(text_id)
        if black:
            cards.truth.pick[text_id] = pick
        else:
            cards.truth.quality[text_id] = rng.gauss(0, 1)
        return text_id

    for topic, (templates, nouns) in TOPICS.items():
        for template in templates:
            add(template, True, topic, pick=1)
        add(TWO_BLANKS.format(topic=topic), True, topic, pick=2)
        for noun in nouns:
            for adjective in ADJECTIVES:
                add(f"{adjective}{noun}".capitalize(), False, topic)
    add(THREE_BLANKS.format(topic="randi"), True, "randi", pick=3)
    # A card whose wording was edited later: same card_id, new text_id (gameplay-logging.md, Identifiers).
    first = cards.rows[cards.whites[0] - 1]
    add(first[4] + " (javított)", False, cards.truth.topic[first[0]], same_card=True)
    return cards


def _gumbel(rng: random.Random) -> float:
    return -math.log(-math.log(rng.random() or 1e-12))


def _utility(rng: random.Random, truth: SampleTruth, white: int, black: int) -> float:
    match = truth.topic[white] == truth.topic[black]
    return QUALITY_WEIGHT * truth.quality[white] + TOPIC_BONUS * match + NOISE * _gumbel(rng)


def _pack_probs(probs: list[float]) -> bytes:
    """decision.probs: float16 per hand card, little-endian, in hand order (gameplay-logging.md)."""
    return struct.pack(f"<{len(probs)}e", *probs)


class _Deck:
    """White cards for one game: dealt without replacement, reshuffled when empty (like the game)."""

    def __init__(self, rng: random.Random, whites: list[int]) -> None:
        self._rng, self._whites, self._cards = rng, whites, []

    def draw(self, n: int, exclude: set[int]) -> list[int]:
        out: list[int] = []
        while len(out) < n:
            if not self._cards:
                self._cards = [c for c in self._whites if c not in exclude]
                self._rng.shuffle(self._cards)
            card = self._cards.pop()
            if card not in exclude and card not in out:
                out.append(card)
        return out


def _simulate_game(rng: random.Random, cards: _Cards, game_id: int, day: int, model_id: int) -> dict[str, list]:
    truth = cards.truth
    players = rng.choices([1, 2, 3, 4, 5], weights=[1, 1, 3, 3, 2])[0]
    vote_mode = rng.random() < 0.5
    self_vote = vote_mode and rng.random() < 0.2  # czar games never allow it (real data)
    rounds = rng.choice([10, 20, 30])
    timer = rng.choice([30, 45, 60])
    has_model = players >= 3 and rng.random() < 0.15
    model_seat = players - 1 if has_model else None
    closed_at = rng.randint(2, rounds) if rng.random() < 0.15 else None

    rows: dict[str, list] = {k: [] for k in ("game", "game_player", "round", "decision", "hand_change", "vote")}
    rows["game"].append((game_id, day, int(vote_mode), int(self_vote), rounds, timer, "-1,107", APP_VERSION))
    for seat in range(players):
        rows["game_player"].append((game_id, seat, model_id if seat == model_seat else None))

    deck = _Deck(rng, cards.whites)
    hands: dict[int, list[int]] = {}
    for seat in range(players):
        hands[seat] = deck.draw(HAND_SIZE, exclude={c for h in hands.values() for c in h})

    black_weights = [PICK_WEIGHTS[truth.pick[b]] for b in cards.blacks]
    t = 0
    for round_no in range(1, rounds + 1):
        black = rng.choices(cards.blacks, weights=black_weights)[0]
        pick = truth.pick[black]
        czar = None if vote_mode else (round_no - 1) % players
        in_use = {c for h in hands.values() for c in h}
        everyone_idle = rng.random() < 0.08
        closing = round_no == closed_at

        submissions: dict[int, list[int]] = {}
        seq_by_seat: dict[int, int] = {}
        for seat in range(players):
            is_model = seat == model_seat
            if everyone_idle or (not is_model and rng.random() < 0.12):
                continue
            if not is_model and rng.random() < 0.12:     # new hand: the whole hand out, ten new cards in
                new = deck.draw(HAND_SIZE, exclude=in_use)
                rows["hand_change"].append((game_id, round_no, seat, seq_by_seat.get(seat, 0),
                                            pack_ids(hands[seat]), pack_ids(new)))
                seq_by_seat[seat] = seq_by_seat.get(seat, 0) + 1
                in_use |= set(new)
                hands[seat] = new
            elif not is_model and rng.random() < 0.03:   # swap one card
                i = rng.randrange(HAND_SIZE)
                new = deck.draw(1, exclude=in_use)
                rows["hand_change"].append((game_id, round_no, seat, seq_by_seat.get(seat, 0),
                                            pack_ids([hands[seat][i]]), pack_ids(new)))
                seq_by_seat[seat] = seq_by_seat.get(seat, 0) + 1
                in_use |= set(new)
                hands[seat][i] = new[0]
            hand = hands[seat]
            if is_model:
                picks = rng.sample(hand, pick)
            else:
                ranked = sorted(hand, key=lambda c: _utility(rng, truth, c, black), reverse=True)
                picks = ranked[:pick]
            submissions[seat] = picks

        for seat in range(players):
            hand, picks = hands[seat], submissions.get(seat)
            p_chosen = probs = None
            if seat == model_seat and picks is not None:
                p_chosen = 1 / len(hand)
                probs = _pack_probs([1 / len(hand)] * len(hand))
            rows["decision"].append((game_id, round_no, seat, pack_ids(hand),
                                     pack_ids(picks) if picks is not None else None, p_chosen, probs))

        if closing:
            outcome, shown, votes = CLOSED, None, []
        elif not submissions:
            outcome, shown, votes = NO_SUBMISSIONS, None, []
        else:
            shown = list(submissions)
            rng.shuffle(shown)
            votes = []
            voters = [czar] if czar is not None else list(range(players))
            for voter in voters:
                if voter != model_seat and rng.random() < 0.1:
                    continue  # didn't vote in time
                allowed = [s for s in shown if s != voter or self_vote or players == 1]
                if not allowed:
                    continue
                if voter == model_seat:
                    target = rng.choice(allowed)
                else:
                    target = max(allowed, key=lambda s: sum(_utility(rng, truth, c, black) for c in submissions[s]))
                votes.append((game_id, round_no, voter, target))
            outcome = DECIDED if votes else NO_DECISION
        eligible = 1 if czar is not None else players
        rows["round"].append((game_id, round_no, t, black, czar, outcome, eligible,
                              pack_ids(shown) if shown is not None else None))
        rows["vote"].extend(votes)

        if closing:
            break
        for seat, picks in submissions.items():  # played cards are replaced from the deck
            in_use = {c for h in hands.values() for c in h}
            kept = [c for c in hands[seat] if c not in picks]
            hands[seat] = kept + deck.draw(HAND_SIZE - len(kept), exclude=in_use)
        t += rng.randint(25, 60)
    return rows


def generate(path: str | os.PathLike, games: int = 40, seed: int = 0, days: int = 14) -> SampleTruth:
    """Write a new gameplay.db with `games` simulated games spread over `days` days (game IDs in date
    order, like the real log) and return the hidden values behind the choices."""
    if games < 1 or days < 1:
        raise ValueError("games and days must be at least 1")
    rng = random.Random(seed)
    cards = _make_cards(rng)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        with conn:
            conn.executemany("INSERT INTO card_text VALUES (?,?,?,?,?)", cards.rows)
            conn.execute("INSERT INTO model (model_id, name, version) VALUES (1, ?, ?)", MODEL)
            for game_id in range(1, games + 1):
                day = FIRST_DAY + (game_id - 1) * days // games
                rows = _simulate_game(rng, cards, game_id, day, model_id=1)
                conn.executemany("INSERT INTO game VALUES (?,?,?,?,?,?,?,?)", rows["game"])
                conn.executemany("INSERT INTO game_player VALUES (?,?,?)", rows["game_player"])
                conn.executemany("INSERT INTO round VALUES (?,?,?,?,?,?,?,?)", rows["round"])
                conn.executemany("INSERT INTO decision VALUES (?,?,?,?,?,?,?)", rows["decision"])
                conn.executemany("INSERT INTO hand_change VALUES (?,?,?,?,?,?)", rows["hand_change"])
                conn.executemany("INSERT INTO vote VALUES (?,?,?,?)", rows["vote"])
    finally:
        conn.close()
    return cards.truth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output file (default: {DEFAULT_OUT})")
    parser.add_argument("--games", type=int, default=40, help="number of games (default: 40)")
    parser.add_argument("--days", type=int, default=14, help="days the games are spread over (default: 14)")
    parser.add_argument("--seed", type=int, default=0, help="random seed; the same seed gives the same file")
    parser.add_argument("--force", action="store_true", help="replace an existing file")
    args = parser.parse_args(argv)

    out: Path = args.out
    if out.exists() and not args.force:
        print(f"{out} already exists; pass --force to replace it.", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    # Written to a temporary file and moved into place, so a failed run leaves no half-written database.
    fd, tmp = tempfile.mkstemp(dir=out.parent, suffix=".db.tmp")
    os.close(fd)
    os.unlink(tmp)
    try:
        generate(tmp, games=args.games, seed=args.seed, days=args.days)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print(f"Wrote {args.games} sample games to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
