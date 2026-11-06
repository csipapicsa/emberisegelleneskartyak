"""Smoke test of the whole product: three phones create a room, join, get approved, and play one round.

It exercises what the unit tests can't reach together: page navigation, the WebSocket protocol between
browser and server, the lobby approval, dealing from the card database, the card hand UI and the
czar's pick.
"""
from __future__ import annotations

import re
import sqlite3
import time

import pytest

from server.gameplay_log import unpack_ids

# Skipped (not an import error) where Playwright isn't installed; see conftest.py.
expect = pytest.importorskip("playwright.sync_api").expect

NAMES = ["Anna", "Bence", "Csilla"]


def submit_cards(page) -> None:
    """Play as many cards as the black card asks for. A card is chosen by tapping it twice; once enough
    are chosen the confirm dialog opens."""
    expect(page.locator(".hand-card[data-id]")).to_have_count(10)
    modal = page.locator("#confirmModal")
    cards = page.locator(".hand-card[data-id]")
    for i in range(3):  # pick 1 to pick 3
        card = cards.nth(i)
        card.click(position={"x": 12, "y": 40})  # left edge: the fanned cards overlap to the right
        card.click(position={"x": 12, "y": 40})
        if modal.is_visible():
            break
    expect(modal).to_be_visible()
    page.locator("#confirmYes").click()
    expect(modal).to_be_hidden()


def logged_round(data_dir) -> sqlite3.Connection:
    """The gameplay log is written by a background thread; wait until the round is in."""
    path = data_dir / "gameplay.db"
    deadline = time.monotonic() + 10
    while True:
        if path.exists():
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            if conn.execute("SELECT name FROM sqlite_master WHERE name = 'round'").fetchone() and \
                    conn.execute("SELECT 1 FROM round WHERE outcome = 0").fetchone():
                return conn
            conn.close()
        assert time.monotonic() < deadline, "the round never reached gameplay.db"
        time.sleep(0.2)


def test_three_players_play_a_round(new_player, server_data_dir):
    admin, *others = players = [new_player(name) for name in NAMES]

    # Admin creates the room.
    admin.goto("/create")
    admin.fill("#username", NAMES[0])
    admin.click("#createBtn")
    expect(admin).to_have_url(re.compile(r"/room/\d{4}$"))
    code = admin.url.rsplit("/", 1)[1]
    expect(admin.locator("#roomCodeDisplay")).to_have_text(code)

    # Two players join with the code and wait for approval.
    for page, name in zip(others, NAMES[1:], strict=True):
        page.goto(f"/join?code={code}")
        page.fill("#username", name)
        page.click("#joinBtn")
        expect(page).to_have_url(re.compile(rf"/room/{code}$"))
        expect(page.locator("#pendingScreen")).to_be_visible()

    # Admin approves both, picks unlimited time and czar mode, and starts the game.
    approve = admin.locator("#pendingList button", has_text="Enged")
    expect(approve).to_have_count(2)
    approve.first.click()
    expect(approve).to_have_count(1)
    approve.first.click()
    expect(admin.locator("#playerList")).to_contain_text(NAMES[2])
    admin.click('#timerSeg button[data-val="0"]')
    admin.click('#votingSeg button[data-val="czar"]')
    expect(admin.locator("#startBtn")).to_be_enabled()
    admin.click("#startBtn")

    for page in players:
        expect(page).to_have_url(re.compile(rf"/game/{code}$"))
        expect(page.locator("#roundEl")).to_contain_text("1 /")
        expect(page.locator("#blackCard")).not_to_be_empty()

    # Everyone plays, the czar included.
    for page in players:
        submit_cards(page)

    # Once all three are in, every phone shows the three anonymous answers.
    for page in players:
        expect(page.locator(".submission-pile")).to_have_count(3)

    # Exactly one player is the czar and gets the clickable piles; they pick another player's answer.
    czars = [p for p in players if p.locator(".czar-pick-hint").count() == 1]
    assert len(czars) == 1, "exactly one czar"
    czar = czars[0]
    pickable = czar.locator(".submission-pile:not(.vote-disabled)")
    expect(pickable).to_have_count(2)  # three submissions, minus the czar's own
    pickable.first.click()

    # Every phone shows the same winner, and the winner is one of the two non-czar players.
    labels = []
    for page in players:
        label = page.locator("#winnerLabel")
        expect(label).to_contain_text("Győztes")
        labels.append(label.inner_text())
    assert len(set(labels)) == 1, labels
    czar_name = NAMES[players.index(czar)]
    assert any(name in labels[0] for name in NAMES if name != czar_name), labels[0]

    # The round is in the anonymous gameplay log: three hands with their picks, the shown order, and the
    # czar's pick as a single vote for another seat.
    if server_data_dir is None:
        return  # an external server whose data directory isn't reachable (CI sets EEK_E2E_DATA_DIR)
    conn = logged_round(server_data_dir)
    (game_id,) = conn.execute("SELECT MAX(game_id) FROM game").fetchone()
    czar_seat, outcome, eligible, shown, black = conn.execute(
        "SELECT czar, outcome, eligible, shown, black FROM round WHERE game_id = ? AND round_no = 1", (game_id,)
    ).fetchone()
    (pick,) = conn.execute("SELECT pick FROM card_text WHERE text_id = ?", (black,)).fetchone()
    assert (outcome, eligible) == (0, 1)
    assert sorted(unpack_ids(shown)) == [0, 1, 2]
    decisions = conn.execute("SELECT hand, picks FROM decision WHERE game_id = ? AND round_no = 1",
                             (game_id,)).fetchall()
    assert len(decisions) == 3
    for hand, picks in decisions:
        assert len(unpack_ids(hand)) == 10 and len(unpack_ids(picks)) == pick
    ((voter, target),) = conn.execute("SELECT voter, target FROM vote WHERE game_id = ? AND round_no = 1",
                                      (game_id,)).fetchall()
    assert voter == czar_seat != target
    conn.close()
