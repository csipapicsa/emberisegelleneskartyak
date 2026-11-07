"""Check a snapshot against the game's rules before anything trains on it.

    python -m ml.validate ml-data/snapshots/2026-11-07_3f2a9c1b0d4e
    python -m ml.validate --first-day 2025-01-01 <snapshot>   # sample data (scripts/make_sample_gameplay_db.py)

Every check is a rule the game guarantees (server/game.py, server/gameplay_log.py). A violation means a
bug in the game or the logger, or a broken export, so the pipeline stops: exit code 1, and the report
(validation.json in the snapshot) names each failed check with examples.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ml.export import read_snapshot
from server.gameplay_log import CLOSED, DECIDED, NO_DECISION, NO_SUBMISSIONS

HAND_SIZE = 10
FIRST_LOG_DAY = 20763   # 2026-11-06, the day gameplay logging went live
REPORT = "validation.json"
MAX_EXAMPLES = 5


@dataclass
class CheckResult:
    name: str
    description: str
    failures: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failures == 0


class _Data:
    """The snapshot's tables, indexed for the checks."""

    def __init__(self, tables: dict[str, list[dict]], first_day: int) -> None:
        self.first_day = first_day
        self.cards = {c["text_id"]: c for c in tables["card_texts"]}
        self.games = {g["game_id"]: g for g in tables["games"]}
        self.rounds = {(r["game_id"], r["round_no"]): r for r in tables["rounds"]}
        self.decisions = tables["decisions"]
        self.hand_changes = tables["hand_changes"]
        self.votes = tables["votes"]
        self.decision = {(d["game_id"], d["round_no"], d["seat"]): d for d in self.decisions}
        self.submitters: dict[tuple, set[int]] = defaultdict(set)
        for d in self.decisions:
            if d["picks"] is not None:
                self.submitters[d["game_id"], d["round_no"]].add(d["seat"])
        self.round_votes: dict[tuple, list[dict]] = defaultdict(list)
        for v in self.votes:
            self.round_votes[v["game_id"], v["round_no"]].append(v)


Check = Callable[[_Data], Iterator[str]]
CHECKS: list[tuple[str, str, Check]] = []


def check(name: str, description: str) -> Callable[[Check], Check]:
    def register(fn: Check) -> Check:
        CHECKS.append((name, description, fn))
        return fn
    return register


def _where(row: dict, seat_key: str = "seat") -> str:
    seat = f" seat {row[seat_key]}" if seat_key in row else ""
    return f"game {row['game_id']} round {row['round_no']}{seat}"


@check("card_refs", "every card ID in rounds, hands, picks and hand changes is in the card catalogue")
def _card_refs(d: _Data) -> Iterator[str]:
    for r in d.rounds.values():
        if r["black"] not in d.cards:
            yield f"{_where(r)}: black card {r['black']} unknown"
    for row in d.decisions:
        for c in (row["hand"] or []) + (row["picks"] or []):
            if c not in d.cards:
                yield f"{_where(row)}: card {c} unknown"
    for row in d.hand_changes:
        for c in row["removed"] + row["added"]:
            if c not in d.cards:
                yield f"{_where(row)}: card {c} unknown"


@check("card_colors", "the round's card is black; hands, picks and hand changes hold white cards only")
def _card_colors(d: _Data) -> Iterator[str]:
    for r in d.rounds.values():
        if r["black"] in d.cards and not d.cards[r["black"]]["black"]:
            yield f"{_where(r)}: round card {r['black']} is white"
    for row in d.decisions:
        if any(c in d.cards and d.cards[c]["black"] for c in row["hand"]):
            yield f"{_where(row)}: black card in hand"
    for row in d.hand_changes:
        if any(c in d.cards and d.cards[c]["black"] for c in row["removed"] + row["added"]):
            yield f"{_where(row)}: black card in hand change"


@check("hand_size", f"every hand has {HAND_SIZE} different cards")
def _hand_size(d: _Data) -> Iterator[str]:
    for row in d.decisions:
        if len(row["hand"]) != HAND_SIZE or len(set(row["hand"])) != len(row["hand"]):
            yield f"{_where(row)}: {len(row['hand'])} cards, {len(set(row['hand']))} different"


@check("picks_in_hand", "picked cards are different cards from the player's hand")
def _picks_in_hand(d: _Data) -> Iterator[str]:
    for row in d.decisions:
        picks = row["picks"]
        if picks is not None and (not set(picks) <= set(row["hand"]) or len(set(picks)) != len(picks)):
            yield f"{_where(row)}: picks {picks} not all in hand"


@check("pick_count", "a submission has as many cards as the black card has blanks")
def _pick_count(d: _Data) -> Iterator[str]:
    for row in d.decisions:
        r = d.rounds.get((row["game_id"], row["round_no"]))
        if row["picks"] is not None and r and r["black"] in d.cards:
            need = d.cards[r["black"]]["pick"]
            if len(row["picks"]) != need:
                yield f"{_where(row)}: {len(row['picks'])} cards for {need} blanks"


@check("rows_belong", "decisions, hand changes and votes belong to a logged round, and seats exist in the game")
def _rows_belong(d: _Data) -> Iterator[str]:
    for table, seat_keys in ((d.decisions, ("seat",)), (d.hand_changes, ("seat",)), (d.votes, ("voter", "target"))):
        for row in table:
            if (row["game_id"], row["round_no"]) not in d.rounds:
                yield f"{_where(row, seat_keys[0])}: no such round"
                continue
            players = d.games[row["game_id"]]["players"]
            for key in seat_keys:
                if not 0 <= row[key] < players:
                    yield f"{_where(row, key)}: {key} {row[key]} outside 0..{players - 1}"
    for (game_id, round_no) in d.rounds:
        if game_id not in d.games:
            yield f"game {game_id} round {round_no}: game missing"


@check("rounds_in_game", "round numbers stay within the game's length, and a closed round is the game's last")
def _rounds_in_game(d: _Data) -> Iterator[str]:
    last: dict[int, int] = defaultdict(int)
    for game_id, round_no in d.rounds:
        last[game_id] = max(last[game_id], round_no)
    for (game_id, round_no), r in d.rounds.items():
        game = d.games.get(game_id)
        if game and not 1 <= round_no <= game["rounds"]:
            yield f"{_where(r)}: game has {game['rounds']} rounds"
        if r["outcome"] == CLOSED and round_no != last[game_id]:
            yield f"{_where(r)}: closed, but round {last[game_id]} follows"


@check("outcome", "the outcome matches the round: 1 nobody submitted, 0 votes cast, 2 or 3 no votes")
def _outcome(d: _Data) -> Iterator[str]:
    for key, r in d.rounds.items():
        submitted, votes = d.submitters[key], d.round_votes[key]
        outcome = r["outcome"]
        if outcome not in (DECIDED, NO_SUBMISSIONS, NO_DECISION, CLOSED):
            yield f"{_where(r)}: unknown outcome {outcome}"
        elif outcome == NO_SUBMISSIONS and (submitted or r["shown"] is not None or votes):
            yield f"{_where(r)}: outcome 1, but {len(submitted)} submissions"
        elif outcome == DECIDED and not votes:
            yield f"{_where(r)}: outcome 0 without a vote"
        elif outcome in (NO_DECISION, CLOSED) and votes:
            yield f"{_where(r)}: outcome {outcome} with {len(votes)} votes"


@check("shown", "the submissions shown are exactly the cards submitted, each once")
def _shown(d: _Data) -> Iterator[str]:
    for key, r in d.rounds.items():
        if r["outcome"] in (DECIDED, NO_DECISION):
            shown = r["shown"] or []
            if len(shown) != len(set(shown)) or set(shown) != d.submitters[key]:
                yield f"{_where(r)}: shown {shown}, submitted {sorted(d.submitters[key])}"


@check("votes", "votes go to submitted cards, at most one per eligible player; self-votes only where allowed")
def _votes(d: _Data) -> Iterator[str]:
    for key, votes in d.round_votes.items():
        r, game = d.rounds.get(key), d.games.get(key[0])
        if r is None or game is None:
            continue
        if len(votes) > r["eligible"]:
            yield f"{_where(r)}: {len(votes)} votes, {r['eligible']} eligible"
        for v in votes:
            if v["target"] not in d.submitters[key]:
                yield f"{_where(v, 'voter')}: voted for seat {v['target']}, who didn't submit"
            if v["voter"] == v["target"] and not game["self_vote"]:
                yield f"{_where(v, 'voter')}: voted for their own card"


@check("czar", "czar mode: a czar each round, the only voter, never picking their own card; vote mode: no czar")
def _czar(d: _Data) -> Iterator[str]:
    for key, r in d.rounds.items():
        game = d.games.get(key[0])
        if game is None:
            continue
        if game["vote_mode"]:
            if r["czar"] is not None:
                yield f"{_where(r)}: czar {r['czar']} in vote mode"
            continue
        if r["czar"] is None or r["eligible"] != 1:
            yield f"{_where(r)}: czar {r['czar']}, eligible {r['eligible']}"
        for v in d.round_votes[key]:
            if v["voter"] != r["czar"]:
                yield f"{_where(v, 'voter')}: voted, but the czar is seat {r['czar']}"
            elif v["target"] == r["czar"]:
                yield f"{_where(v, 'voter')}: the czar picked their own card"


@check("hand_changes", "hand changes are numbered 0, 1, 2, ... and the last one ends in the logged hand")
def _hand_changes(d: _Data) -> Iterator[str]:
    steps: dict[tuple, list[dict]] = defaultdict(list)
    for row in d.hand_changes:
        steps[row["game_id"], row["round_no"], row["seat"]].append(row)
    for key, rows in steps.items():
        rows.sort(key=lambda row: row["seq"])
        last = rows[-1]
        if [row["seq"] for row in rows] != list(range(len(rows))):
            yield f"{_where(last)}: sequence {[row['seq'] for row in rows]}"
        decision = d.decision.get(key)
        if decision is None:
            yield f"{_where(last)}: hand change without a decision"
            continue
        hand = set(decision["hand"])
        if len(last["removed"]) != len(last["added"]) or not set(last["added"]) <= hand or set(last["removed"]) & hand:
            yield f"{_where(last)}: last change doesn't end in the logged hand"


@check("days", "game days fall between the start of logging and today")
def _days(d: _Data) -> Iterator[str]:
    today = (date.today() - date(1970, 1, 1)).days
    for game in d.games.values():
        if not d.first_day <= game["day"] <= today + 1:
            yield f"game {game['game_id']}: day {game['day']} ({date(1970, 1, 1) + timedelta(days=game['day'])})"


def validate(tables: dict[str, list[dict]], first_day: int = FIRST_LOG_DAY) -> list[CheckResult]:
    """Run every check. first_day: the earliest plausible game day (days since 1970-01-01)."""
    data = _Data(tables, first_day)
    results = []
    for name, description, fn in CHECKS:
        result = CheckResult(name, description)
        for problem in fn(data):
            result.failures += 1
            if len(result.examples) < MAX_EXAMPLES:
                result.examples.append(problem)
        results.append(result)
    return results


def validate_snapshot(folder: str | Path, write_report: bool = True,
                      first_day: int = FIRST_LOG_DAY) -> list[CheckResult]:
    """Validate a snapshot folder; the report is written next to the data as validation.json."""
    folder = Path(folder)
    manifest, tables = read_snapshot(folder)
    results = validate(tables, first_day)
    if write_report:
        report = {"content_hash": manifest["content_hash"], "ok": all(r.ok for r in results),
                  "checks": [asdict(r) for r in results]}
        (folder / REPORT).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return results


def require_valid(folder: str | Path) -> dict:
    """The snapshot's manifest, if validate_snapshot passed on exactly this data; otherwise ValueError.
    Everything downstream (benchmark, training) calls this, so nothing runs on unchecked data."""
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    report_path = folder / REPORT
    if not report_path.is_file():
        raise ValueError(f"{folder} hasn't been validated; run python -m ml.validate {folder}")
    report = json.loads(report_path.read_text())
    if report.get("content_hash") != manifest["content_hash"] or not report.get("ok"):
        failed = [c["name"] for c in report.get("checks", []) if c.get("failures")]
        raise ValueError(f"{folder} failed validation: {', '.join(failed) or 'report does not match the data'}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("snapshot", type=Path, help="snapshot folder written by ml.export")
    parser.add_argument("--first-day", type=date.fromisoformat, default=None,
                        help="earliest plausible game date, YYYY-MM-DD (default: 2026-11-06, when logging began)")
    args = parser.parse_args(argv)
    first_day = FIRST_LOG_DAY if args.first_day is None else (args.first_day - date(1970, 1, 1)).days
    try:
        results = validate_snapshot(args.snapshot, first_day=first_day)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Validation failed to run: {exc}", file=sys.stderr)
        return 1
    for r in results:
        print(f"{'ok  ' if r.ok else 'FAIL'} {r.name}: {r.description}" + ("" if r.ok else f" ({r.failures})"))
        for example in r.examples:
            print(f"       {example}")
    failed = [r.name for r in results if not r.ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
