"""/metrics: what Prometheus scrapes, and that the public can't read it through nginx."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from server import bot, metrics
from server import main as main_mod
from server.room import Player, create_room, delete_room
from tests.test_editions import make_master


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main_mod, "get_conn", make_master)
    monkeypatch.setenv("EEK_DISABLE_ROOM_CLEANUP", "1")
    return TestClient(main_mod.app)


def scrape(client) -> dict[tuple[str, tuple], float]:
    """{(sample name, sorted labels): value} from /metrics, parsed like Prometheus does."""
    res = client.get("/metrics")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    return {(s.name, tuple(sorted(s.labels.items()))): s.value
            for fam in text_string_to_metric_families(res.text) for s in fam.samples}


def value(samples, name, **labels) -> float:
    return samples.get((name, tuple(sorted(labels.items()))), 0.0)


def test_metrics_lists_the_game_metrics_and_process_metrics(client):
    samples = scrape(client)
    names = {name for name, _ in samples}
    for expected in ("eek_build_info", "eek_rooms", "eek_players", "eek_websocket_connections",
                     "eek_computer_player_decisions_total", "eek_games_started_total",
                     "eek_model_requests_total", "process_resident_memory_bytes"):
        assert expected in names, expected


@pytest.mark.parametrize("header", ["X-Real-IP", "X-Forwarded-For"])
def test_requests_through_nginx_get_404(client, header):
    """nginx adds these headers to everything it forwards; Prometheus on 127.0.0.1 doesn't send them."""
    assert client.get("/metrics", headers={header: "203.0.113.7"}).status_code == 404


def test_http_requests_are_counted_by_route_template_not_raw_path(client):
    before = scrape(client)
    client.get("/api/room/ABCD")
    client.get("/api/room/WXYZ")
    client.get("/no-such-page-at-all")
    after = scrape(client)

    template = [k for k in after if k[0] == "eek_http_requests_total" and ("route", "/api/room/{code}") in k[1]]
    assert template, "the room route is counted under its template"
    assert sum(after[k] - before.get(k, 0) for k in template) == 2
    assert not any("ABCD" in str(k) or "WXYZ" in str(k) for k in after), "raw codes would explode the series"
    # Unknown pages land on the catch-all page route: still one series, whatever the URL was.
    catch_all = dict(method="GET", route="/{full_path:path}", status="404")
    assert value(after, "eek_http_requests_total", **catch_all) - value(before, "eek_http_requests_total", **catch_all) == 1
    assert not any("no-such-page" in str(k) for k in after)
    assert value(after, "eek_http_request_duration_seconds_count", method="GET", route="/api/room/{code}") \
        - value(before, "eek_http_request_duration_seconds_count", method="GET", route="/api/room/{code}") == 2


def test_live_state_reads_rooms_players_and_bot_stats(client, monkeypatch):
    monkeypatch.setattr(bot, "stats", {"model": 7, "random": 2})
    room = create_room("MTR1", "admin-sid", "Anna")
    try:
        room.players["bot-sid"] = Player(session_id="bot-sid", username="Gép", is_bot=True)
        room.players["gone-sid"] = Player(session_id="gone-sid", username="Béla", connected=False)
        samples = scrape(client)
    finally:
        delete_room("MTR1")

    assert value(samples, "eek_rooms", phase="lobby") >= 1
    assert value(samples, "eek_players", kind="computer") >= 1
    assert value(samples, "eek_players", kind="human_disconnected") >= 1
    assert value(samples, "eek_players", kind="human_connected") >= 1
    assert value(samples, "eek_computer_player_decisions_total", decision="model") == 7
    assert value(samples, "eek_computer_player_decisions_total", decision="random") == 2


def test_build_info_carries_the_version(client):
    samples = scrape(client)
    assert any(name == "eek_build_info" and v == 1 for (name, _), v in samples.items())


def test_model_requests_are_counted_with_outcome(client, monkeypatch):
    monkeypatch.setenv(bot.MODEL_URL_ENV, "http://model.invalid")
    before = scrape(client)

    monkeypatch.setattr(bot, "_post", lambda url, body, timeout: {
        "model": "pick", "version": "6", "scores": [1.0, 2.0], "probs": [0.4, 0.6]})
    assert asyncio.run(bot.ask("Fekete _.", ["a", "b"], timeout=5)) is not None

    def broken(url, body, timeout):
        raise ConnectionError("down")
    monkeypatch.setattr(bot, "_post", broken)
    assert asyncio.run(bot.ask("Fekete _.", ["a", "b"], timeout=5)) is None

    after = scrape(client)
    for outcome in ("ok", "error"):
        assert value(after, "eek_model_requests_total", outcome=outcome) \
            - value(before, "eek_model_requests_total", outcome=outcome) == 1
    assert value(after, "eek_model_request_duration_seconds_count") \
        - value(before, "eek_model_request_duration_seconds_count") == 2


def test_websocket_count_follows_the_connection_manager(client, monkeypatch):
    monkeypatch.setattr(main_mod.manager, "_sockets", {"R1": {"a": object(), "b": object()}, "R2": {"c": object()}})
    assert value(scrape(client), "eek_websocket_connections") == 3


def test_route_of_without_a_matched_route():
    assert metrics.route_of({}) == "unmatched"
