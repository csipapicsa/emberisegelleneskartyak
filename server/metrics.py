"""Prometheus metrics for the game, served at /metrics.

What is measured:

- HTTP requests: count and latency per route template (``/api/rooms/{code}``, never the raw path, so the
  number of series stays bounded) and status code.
- Live state, read at scrape time (no counters to keep in sync): rooms per phase, players (humans connected or
  not, computer players), open WebSockets.
- Games started.
- The computer player: its decisions (model vs random, the same numbers as /healthz) and every call to the
  model service (outcome and latency).

/metrics is for Prometheus on the server itself: requests that came through nginx (they carry X-Real-IP or
X-Forwarded-For) get a 404, so the public can't read it even though nginx forwards every path. The nginx
configs block the path too (deploy/nginx).

The metrics live in their own registry, so importing this module twice (tests) doesn't register them twice.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

# No extra *_created series per counter: Prometheus doesn't need them, and they double the series count.
disable_created_metrics()
REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

PROXY_HEADERS = ("x-real-ip", "x-forwarded-for")
PHASES = ("lobby", "submitting", "revealed", "round_ending", "finished")

BUILD_INFO = Gauge("eek_build_info", "The running commit (value is always 1).", ["version"], registry=REGISTRY)
HTTP_REQUESTS = Counter(
    "eek_http_requests", "HTTP requests by route template and status code.",
    ["method", "route", "status"], registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "eek_http_request_duration_seconds", "HTTP request latency by route template.",
    ["method", "route"], registry=REGISTRY,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
GAMES_STARTED = Counter("eek_games_started", "Games started.", registry=REGISTRY)
MODEL_REQUESTS = Counter(
    "eek_model_requests", "Calls from the computer player to the model service, by outcome.",
    ["outcome"], registry=REGISTRY,
)
MODEL_DURATION = Histogram(
    "eek_model_request_duration_seconds", "Latency of the model service as the game sees it.",
    registry=REGISTRY, buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15),
)
# Pre-create the label sets the dashboard and alerts use, so they exist (as 0) before the first event.
for _outcome in ("ok", "error"):
    MODEL_REQUESTS.labels(_outcome)


class _LiveState:
    """Reads rooms, players, sockets and the computer player's counts when Prometheus scrapes."""

    def __init__(self, rooms: Callable[[], Iterable], sockets: Callable[[], int], bot_stats: Callable[[], dict]):
        self._rooms, self._sockets, self._bot_stats = rooms, sockets, bot_stats

    def collect(self):
        rooms = list(self._rooms())
        by_phase = dict.fromkeys(PHASES, 0)
        players = {"human_connected": 0, "human_disconnected": 0, "computer": 0}
        for room in rooms:
            by_phase[room.phase] = by_phase.get(room.phase, 0) + 1
            for p in room.players.values():
                kind = "computer" if p.is_bot else ("human_connected" if p.connected else "human_disconnected")
                players[kind] += 1
        g = GaugeMetricFamily("eek_rooms", "Rooms in memory, by phase.", labels=["phase"])
        for phase, n in by_phase.items():
            g.add_metric([phase], n)
        yield g
        g = GaugeMetricFamily("eek_players", "Players in rooms, by kind.", labels=["kind"])
        for kind, n in players.items():
            g.add_metric([kind], n)
        yield g
        yield GaugeMetricFamily("eek_websocket_connections", "Open WebSocket connections.", value=self._sockets())
        c = CounterMetricFamily("eek_computer_player_decisions",
                                "How the computer player decided: by the model, or at random as a fallback.",
                                labels=["decision"])
        stats = self._bot_stats()
        for decision in ("model", "random"):
            c.add_metric([decision], stats.get(decision, 0))
        yield c


_live_state: _LiveState | None = None


def register_live_state(rooms: Callable[[], Iterable], sockets: Callable[[], int],
                        bot_stats: Callable[[], dict]) -> None:
    """Called once by server.main; a second call (a test re-importing the app) replaces the first."""
    global _live_state
    if _live_state is not None:
        REGISTRY.unregister(_live_state)
    _live_state = _LiveState(rooms, sockets, bot_stats)
    REGISTRY.register(_live_state)


def observe_request(method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method, route, str(status)).inc()
    HTTP_DURATION.labels(method, route).observe(seconds)


def observe_model_request(ok: bool, seconds: float) -> None:
    MODEL_REQUESTS.labels("ok" if ok else "error").inc()
    MODEL_DURATION.observe(seconds)


def route_of(scope: dict) -> str:
    """The matched route's template, so /api/rooms/AB12 and /api/rooms/CD34 count as one series."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if path else "unmatched"


def is_proxied(headers) -> bool:
    return any(h in headers for h in PROXY_HEADERS)


def exposition() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

