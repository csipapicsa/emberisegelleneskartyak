# Emberiségellenes Kártyák

[![CI](https://github.com/csipapicsa/emberisegelleneskartyak/actions/workflows/ci.yml/badge.svg)](https://github.com/csipapicsa/emberisegelleneskartyak/actions/workflows/ci.yml)

A mobile-first, real-time multiplayer party card game in Hungarian, in the style of *Cards Against Humanity*.
Players join a room from their phones, everyone answers a black prompt card with a white card from their hand,
and the czar (or a group vote) picks the funniest answer.

**Live:** [emberisegelleneskartyak.hu](https://emberisegelleneskartyak.hu) · about 30 games a day (November 2026)

Personal project: designed, built and run by one developer on a self-managed Linux server.

## Features

- **Rooms and lobby:** 4-digit room codes, admin approval of joining players, card pack selection, round count and timer.
- **Real-time game over WebSockets:** czar or voting mode, multi-pick prompts (pick 2 / pick 3), per-phase timers,
  reconnect after a dropped connection, inactivity kick, anonymous reveal with the author shown after the pick.
- **Card decks:** many overlapping packs, deduplicated per game. A least-seen weighted shuffle keeps rarely dealt
  cards coming up.
- **Community cards:** players submit and vote on card ideas; approved ones become a playable pack.
- **Extras:** in-room text chat, WebRTC voice chat (TURN relay), printable card PDFs, card search and feedback.
- **Privacy by design:** no raw IP addresses stored (purpose-bound HMAC pseudonyms), self-hosted analytics,
  log retention limits.
- **Training data for a computer player:** every round's decisions go to an anonymous log
  ([docs/ml/](docs/ml/gameplay-logging.md)), the input for a computer player with a full ML lifecycle
  ([roadmap](docs/ml/roadmap.md)): MLflow tracking and registry, training on GCP, serving on the own server.

## Architecture

```mermaid
flowchart LR
    B[Phone / browser<br>vanilla JS] -- HTTPS + WSS --> N[nginx<br>TLS, reverse proxy]
    B -. WebRTC voice .-> T[coturn<br>TURN relay]
    N --> A[FastAPI + uvicorn<br>one process]
    A --> R[(In-memory rooms<br>game state)]
    A --> C[(card_master.db<br>read-only cards)]
    A --> M[(SQLite: messages, chat,<br>analytics, card_seen)]
    A --> G[(gameplay.db<br>anonymous ML data)]
    S[systemd timers] --> BK[DB backups,<br>PDF cleanup]
```

- **Backend:** Python 3.11, FastAPI, native WebSockets. Game state lives in memory; one process serves everything.
- **Frontend:** plain HTML, CSS and JavaScript, no framework and no build step.
- **Data:** SQLite. The card database is built offline and uploaded as a file, which the app opens read-only.
- **Operations:** systemd units and timers, nginx with Let's Encrypt, daily database backups, rootless Podman
  containers. Templates are in [deploy/](deploy/).

The full picture (game engine, data, interfaces) is in [docs/architecture.md](docs/architecture.md).

### Delivery pipeline

```mermaid
flowchart LR
    PR[pull request] --> CI[CI: lint, tests, coverage,<br>secret scan, audit,<br>image build + e2e]
    CI -- merge to main --> P[publish the tested image<br>to GHCR]
    P -- auto-update --> ST[staging<br>health-gated, auto rollback]
    ST -- manual promotion --> PROD[production]
```

Every merge to `main` deploys to a password-protected staging site within minutes; the server pulls the image,
so CI never gets access to it. Production is promoted by hand.

## Engineering decisions

- **FastAPI with native WebSockets.** The game is a stream of small real-time events between a few phones and
  one server; async Python handles that in one process without a message broker.
- **Game state in memory, in one process.** Rooms are short-lived and the server is the single source of
  truth, so there is no database round trip per move. The cost: a restart ends running games, and the app
  can't scale beyond one instance without moving rooms to a shared store such as Redis. At about 30 games a
  day that trade-off is the right one.
- **SQLite, one file per concern.** Low write volume, no database server to run, trivial backups. Separate
  files keep retention rules and backups independent. PostgreSQL becomes worth it with several app instances.
- **No frontend framework.** A handful of mobile pages, no build step; what the browser runs is what is in the
  repository.
- **Pull-based, rootless deploys.** Staging pulls tested images from the registry under an unprivileged user,
  and a container that doesn't become healthy is rolled back automatically.
- **Privacy first.** No raw IP addresses (purpose-bound HMAC pseudonyms), retention enforced in code, and the ML
  log has no player identifiers at all.
- **What CI does and doesn't prove.** It runs the game rules, the API and a full browser round against the
  production image, and checks dependencies and secrets. It doesn't test load, the server configuration or
  restores from backup; those are on the [to-do list](docs/TODO.md).

## Quickstart

```bash
git clone https://github.com/csipapicsa/emberisegelleneskartyak.git && cd emberisegelleneskartyak
python3.11 -m venv venv
venv/bin/pip install -r requirements-dev.txt   # lock file: every package pinned with hashes
venv/bin/python scripts/make_sample_db.py     # made-up cards; the real card texts are not in the repo
venv/bin/uvicorn server.main:app --port 8010
```

Open <http://localhost:8010>, create a room, and join it from a second browser window to play.

Optional settings come from environment variables (see [deploy/privacy.env.example](deploy/privacy.env.example)).
`EEK_DATA_DIR` moves all runtime data (the SQLite databases and generated PDFs) out of the code directory, e.g.
to a container volume; by default it stays in the repository root.
Without them the IP-based features (card idea voting, visitor statistics) and the admin statistics page stay off.

### With Docker or Podman

```bash
mkdir -p data && python3 scripts/make_sample_db.py --out data/database/card_master.db && chmod -R a+rwX data
docker build -t emberisegellenes .
docker run -p 127.0.0.1:8010:8010 -v ./data:/data emberisegellenes
```

The image runs as a non-root user and keeps all runtime data in the `/data` volume. Every merge to `main`
publishes the tested image as `ghcr.io/csipapicsa/emberisegelleneskartyak:sha-<commit>` (and `:main`).
`/healthz` reports whether the app can serve a game and which commit is running.

## Tests

```bash
venv/bin/python -m pytest                      # everything
venv/bin/python -m pytest -m "not local_data" --cov  # what CI runs: sample cards, coverage gate
scripts/e2e_in_container.sh                    # browser end-to-end test in the Playwright container
venv/bin/python -m ruff check server tests scripts
```

- About 450 pytest tests: game rules and phase transitions, timers, reconnects, API endpoints, privacy, backups.
- JavaScript tests for the client code (hand swiping, voice chat, ads) run with `node --test tests/js/`, and pytest
  also runs them when Node is installed.
- An end-to-end test drives three headless Chromium "phones" through a full round against a real server (in CI:
  the production container image itself):
  create a room, join, admin approval, play cards, czar pick, winner on every screen. It runs in CI, in the
  Playwright container ([scripts/e2e_in_container.sh](scripts/e2e_in_container.sh)), or directly after
  `python -m playwright install --with-deps chromium`.
- Tests marked `local_data` check the real card database, for example that every card fits on a printed card.
- CI (GitHub Actions) runs on every pull request, on pushes to `main` and weekly: lint (ruff), tests (Python and
  Node, on the sample cards) with a coverage gate, the browser end-to-end test, a secret scan over the full git history (gitleaks) and a dependency vulnerability
  audit (pip-audit). Dependabot opens weekly update PRs.

## Project structure

```
server/        FastAPI app: routes and WebSocket handler (main.py), game logic, rooms, deck, databases
static/        frontend pages, CSS and JS, served by the app
tests/         pytest suite; tests/js/ holds the Node tests for the client code
scripts/       backups, cleanup jobs, sample card database
deploy/        container (Quadlet) units, systemd timers, nginx and logrotate templates, environment example
docs/          architecture, operations, feature and ML docs, dated worklogs (index: docs/README.md)
config.json    game settings (hand size, timer and round options, room limits)
```

Security issues: see [SECURITY.md](SECURITY.md).

## License and card content

The code is licensed under [GPL-3.0-or-later](LICENSE).

The game is based on *Cards Against Humanity*, whose cards are licensed under
[CC BY-NC-SA 2.0](https://creativecommons.org/licenses/by-nc-sa/2.0/). The translated card texts are not part of
this repository. This project is not affiliated with Cards Against Humanity LLC.
