"""Load test for the model service: does the computer player finish its turn in time with brand-new cards?

    python -m ml.serve_bench --url http://127.0.0.1:8020              # 5 games x 10 rounds, worst case
    python -m ml.serve_bench --url http://127.0.0.1:8020 --games 10 --budget 15

Each simulated turn is the bot's worst case (server/bot.py): score its hand, ask for a new hand the maximum
number of times (3), scoring every new hand, then score the other players' submissions to vote. Every card
text is new (random words plus a unique tag), so every embedding is computed on the spot, none comes from
the cache. All games play at the same time, like rooms on the server. The test passes if every turn
finishes within --budget seconds (default 15, the shortest round timer); exit code 1 if not.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

WORDS = ("anyós", "kecske", "nyugdíj", "Viagra", "pálinka", "parlament", "lángos", "szomszéd", "adóbevallás",
         "gumikacsa", "influenszer", "fogorvos", "hajléktalan", "kísértet", "macska", "zokni", "polgármester",
         "lakodalom", "fingás", "metró", "kolbász", "tinder", "unokatestvér", "pornó", "horoszkóp", "tüntetés")
MAX_REFRESHES = 3
Post = Callable[[dict], dict]


@dataclass
class Result:
    calls: list[float] = field(default_factory=list)   # seconds per /score request
    turns: list[float] = field(default_factory=list)   # seconds per whole turn
    errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def http_post(url: str, timeout: float) -> Post:
    def post(body: dict) -> dict:
        req = urllib.request.Request(f"{url.rstrip('/')}/score", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    return post


def new_card(rng: random.Random, tag: str, black: bool = False) -> str:
    words = " ".join(rng.sample(WORDS, rng.randint(2, 5)))
    return f"{words.capitalize()} ({tag}): ________." if black else f"{words.capitalize()} {tag}."


def play_game(post: Post, game: int, rounds: int, players: int, result: Result, seed: int = 0) -> None:
    rng = random.Random(seed * 1000 + game)
    for r in range(rounds):
        tag = f"g{game}r{r}"
        black = new_card(rng, tag, black=True)
        started = time.perf_counter()
        try:
            for attempt in range(1 + MAX_REFRESHES):  # the first hand plus every new hand: all unseen cards
                hand = [new_card(rng, f"{tag}h{attempt}c{i}") for i in range(10)]
                _timed(post, {"black": black, "cards": hand}, result)
            others = [new_card(rng, f"{tag}s{i}") for i in range(players - 1)]
            _timed(post, {"black": black, "cards": others}, result)
        except Exception as exc:  # noqa: BLE001 - a failed turn is a result, not a crash
            with result.lock:
                result.errors.append(f"game {game} round {r}: {type(exc).__name__}: {exc}")
            continue
        with result.lock:
            result.turns.append(time.perf_counter() - started)


def _timed(post: Post, body: dict, result: Result) -> dict:
    started = time.perf_counter()
    answer = post(body)
    with result.lock:
        result.calls.append(time.perf_counter() - started)
    if len(answer.get("scores", [])) != len(body["cards"]):
        raise ValueError(f"expected {len(body['cards'])} scores, got {answer.get('scores')!r:.80}")
    return answer


def run(post: Post, games: int = 5, rounds: int = 10, players: int = 5, seed: int = 0) -> Result:
    result = Result()
    with ThreadPoolExecutor(games) as pool:
        for future in [pool.submit(play_game, post, g, rounds, players, result, seed) for g in range(games)]:
            future.result()
    return result


def summary(result: Result, budget: float) -> tuple[bool, str]:
    def stats(xs: list[float]) -> str:
        if not xs:
            return "none"
        s = sorted(xs)
        return (f"p50 {s[len(s) // 2]:.2f}s  p95 {s[min(len(s) - 1, int(len(s) * 0.95))]:.2f}s  "
                f"max {s[-1]:.2f}s  (n={len(s)})")
    slow = [t for t in result.turns if t > budget]
    ok = not result.errors and not slow and bool(result.turns)
    lines = [f"/score calls: {stats(result.calls)}", f"whole turns: {stats(result.turns)}",
             f"budget {budget:.0f}s: {'PASS' if ok else 'FAIL'}"
             + (f", {len(slow)} turns over budget" if slow else "")
             + (f", {len(result.errors)} errors" if result.errors else "")]
    lines += [f"  {e}" for e in result.errors[:5]]
    return ok, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8020")
    parser.add_argument("--games", type=int, default=5, help="games playing at the same time")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--players", type=int, default=5, help="players per game (the vote sees players - 1)")
    parser.add_argument("--budget", type=float, default=15.0, help="seconds a whole turn may take")
    args = parser.parse_args(argv)
    print(f"{args.games} games x {args.rounds} rounds, {args.players} players, worst case: "
          f"{1 + MAX_REFRESHES} hands + 1 vote per turn, every card new", flush=True)
    ok, text = summary(run(http_post(args.url, args.budget), args.games, args.rounds, args.players), args.budget)
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
