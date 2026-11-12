"""One phone against the computer player: the admin adds it in the lobby, and they play two rounds in czar
mode, one with each of them as the czar. Covers the lobby button, the computer player's submission and its
czar pick through a (fake) model service over HTTP, and the model seat in the gameplay log."""
from __future__ import annotations

import re

import pytest

from server.gameplay_log import unpack_ids, unpack_probs
from tests.e2e.test_play_round import logged_round, submit_cards

expect = pytest.importorskip("playwright.sync_api").expect


def test_one_player_against_the_computer(new_player, server_data_dir):
    page = new_player("Anna")
    page.goto("/create")
    page.fill("#username", "Anna")
    page.click("#createBtn")
    expect(page).to_have_url(re.compile(r"/room/\d{4}$"))
    code = page.url.rsplit("/", 1)[1]

    # One computer player, then the button goes away (max_bots is 1).
    add = page.locator("#addBotBtn")
    expect(add).to_be_visible()
    add.click()
    expect(page.locator("#playerList")).to_contain_text("🤖 Gép")
    expect(add).to_be_hidden()
    page.click('#timerSeg button[data-val="0"]')
    page.click('#votingSeg button[data-val="czar"]')
    expect(page.locator("#startBtn")).to_be_enabled()
    page.click("#startBtn")
    expect(page).to_have_url(re.compile(rf"/game/{code}$"))
    expect(page.locator("#playersPanel")).to_contain_text("🤖 Gép")

    # Round 1: Anna is the czar. The computer player has already played; Anna plays and picks its card.
    expect(page.locator("#roundEl")).to_contain_text("1 /")
    submit_cards(page)
    expect(page.locator(".submission-pile")).to_have_count(2)
    page.locator(".submission-pile:not(.vote-disabled)").first.click()
    expect(page.locator("#winnerLabel")).to_contain_text("Gép")

    # Round 2: the computer player is the czar; with self-votes off it has to pick Anna's card.
    expect(page.locator("#roundEl")).to_contain_text("2 /", timeout=15_000)
    submit_cards(page)
    expect(page.locator("#winnerLabel")).to_contain_text("Anna", timeout=15_000)

    if server_data_dir is None:
        return
    conn = logged_round(server_data_dir)
    (game_id,) = conn.execute("SELECT MAX(game_id) FROM game").fetchone()
    model_seats = conn.execute("SELECT seat FROM game_player WHERE game_id = ? AND model_id IS NOT NULL",
                               (game_id,)).fetchall()
    assert len(model_seats) == 1, "the computer player's seat is a model seat"
    (seat,) = model_seats[0]
    name, version = conn.execute("SELECT m.name, m.version FROM game_player g JOIN model m USING (model_id) "
                                 "WHERE g.game_id = ? AND g.seat = ?", (game_id, seat)).fetchone()
    assert name == "pick-model" and version != "unknown", "the model answered (fake service: 'e2e')"
    hand, picks, probs = conn.execute("SELECT hand, picks, probs FROM decision WHERE game_id = ? AND round_no = 1 "
                                      "AND seat = ?", (game_id, seat)).fetchone()
    assert len(unpack_probs(probs)) == 10 and unpack_ids(picks)[0] in unpack_ids(hand)
    conn.close()
