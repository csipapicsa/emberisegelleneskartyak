# Open work

In priority order within each section. Finished work moves to the worklogs.

## Next steps (from the project review, 2026-11-06)

1. **Production in its container** (Phase 2.4): `eek` user, data in `/srv/eek/prod`, the `:prod` image tag.
   Security: the game stops running under an administrative account (details in the private hardening plan).
   Clean-up: all data moves out of the code folder.
   - [x] Container, network, timers that follow the data, Promote workflow behind an approval (GitHub
         environment `production`), runbook: [runbooks/deploy.md](runbooks/deploy.md) (2026-11-12).
   - [x] Rehearsal: the production image on a copy of production's databases served the same editions and
         played a full three-player browser round (2026-11-12).
   - [ ] The cutover itself: needs root and a quiet moment (runbook, "One-time: move production into its
         container"). Keep the old service for rollback.
   - [ ] Rollback drill: the previous image to production and back (the Promote workflow with an older commit).
   - [ ] Backup restore drill: restore into a temporary folder, `PRAGMA integrity_check`, start the app on it.
   - [ ] CI deploy check: after a merge, wait until staging's `/healthz` shows the new commit (GitHub
         environment `staging`).
   - [x] Disk: 93% → 84% (local LLM models removed). Still to free: pip caches, old images (~5 GB).
2. **ML lifecycle with the computer player**: [ml/roadmap.md](ml/roadmap.md), step by step in
   [ml/implementation-plan.md](ml/implementation-plan.md), the computer player in
   [ml/computer-player.md](ml/computer-player.md).
   - [x] Milestones 1–3 (2026-11-07): MLflow server, local pipeline, the same training as a Cloud Run Job.
   - [x] Milestone 4 (2026-11-12): model service for `challenger` (staging) and `champion` (production),
         computer player (one per room: `max_bots`), promotion and rollback (`python -m ml.promote`),
         pick-model v6 is the first champion. Worst-case turn with brand-new cards, 5 games at once: 3.9 s
         (budget 15 s).
   - [x] Data variants A/B/C (human picks / + games with a computer player / + vote winners) with a clean
         evaluation set and a paired bootstrap in every cycle. First result: vote winners don't help yet.
   - [x] The computer player is live on production (2026-11-12), playing `champion`; the model service also
         serves staging's `challenger`.
   - [ ] Milestone 5, monitoring: the computer player's model/random counts and its win rate in blind votes
         per model version (the online metric for promotions).
   - [ ] Multi-blank cards (pick 2–3): the computer player sends its best cards in score order, but the model
         doesn't know which blank a card fills (it was trained on one-blank cards only). Measured 2026-11-12 on
         531 human 2-blank submissions (champion v6): "best card first" reproduces the human order 51.6% of the
         time (a coin flip); scoring each card against its blank's own sentence reproduces it only 13.2% [10.3,
         16.1], i.e. strongly reversed. Understand why before using it (likely the model rewards the less
         obvious card, which fights grammatical fit), then train blank-aware examples from the multi-blank rounds
         (the log has picks in blank order). Changes the training task, so it's its own step.
   - [ ] Later: several computer players per room (raise `max_bots`), different model versions at one table.
   - [ ] Later: train only in GCP (the nightly timer calls `python -m ml.cloud --register`; the local
         training stays as the CI-tested fallback), then Terraform for the GCP resources
         ([ml/gcp-setup.md](ml/gcp-setup.md) lists them).
   - [ ] Later: improve the pick model. It only matches the popularity baseline (test MRR 0.399 vs 0.392,
         difference not significant). Ideas: learning curve (does more data help?), position and recency
         features, the black card × white card fit (experiment notebooks: `python -m ml.notebooks`), an LLM
         as a ceiling probe, predicting the vote winner instead of the pick.
3. Container image vulnerability scan (Trivy), report only at first, together with the Dependabot PRs.
4. Metrics: requests, 5xx rate, p95 latency, open WebSockets → Prometheus → Grafana, with one alert.
5. A small WebSocket load test (10 → 25 → 50 clients): where the first bottleneck is.
6. README: an "Engineering decisions" section (why SQLite, why rooms live in one process, the limits, what CI
   protects and what it doesn't) and an architecture diagram.

## Reconnecting on phones (2026-11-07)

Switching apps on a phone suspends the page and drops the WebSocket. The player can't come back, and the
others wait for someone who is gone.

- [ ] Measure first: how many connections drop during a game, and how many come back (anonymous analytics).
- [ ] Reconnect automatically with the same session (`visibilitychange` / `online` events).
- [ ] A grace period before kicking; a round shouldn't wait for someone who left.
- [ ] Tests: server (pytest) and end-to-end (a client that drops and returns).

## Containers and staging (2026-11-06)

- [x] Staging is live (`staging.emberisegelleneskartyak.hu`), following `:main` (podman auto-update, 5 min).
- [x] Locked dependencies: `requirements.in` → `requirements.txt` (pip-compile with hashes), Dependabot.
      Production had run starlette 1.0.0 with 5 known vulnerabilities; the CI audit hadn't seen it because
      it didn't check the installed versions.
- [x] Vote mode: when time ran out with no votes, the hands weren't refilled.
- [ ] WebSocket: when browsers close at the same moment (the end of an e2e test), the endpoint's loop calls
      receive on a closed socket ("Need to call accept first" traceback). Harmless for the game; catch it and
      test it.
- [ ] Staging: the Umami script counts staging traffic in the production statistics.

## Backups (reviewed 2026-11-06)

Daily at 03:05 (`emberisegellenes-db-backup.timer` → `scripts/backup_databases.py`), kept 30 days; every
`*.db` in the data folder and `database/`. `backups/` is about 181 MB.

- [ ] Unused databases: `cards.db`, `cards_safety_2026_07_11.db`, `cards_safety_2026_07_27.db` (replaced by
      `card_master.db`; no code reads them), 30 copies a day, ~52 MB. Archive once, delete, stop backing up.
- [ ] An explicit list instead of a glob: which databases are backed up and why.
- [ ] `card_master.db` changes only on upload: 12 copies (65 MB), 4 different. Back it up only when it changed.
- [x] After the move to the container the data lives in `/srv/eek/prod`: `eek-prod-db-backup.timer` backs it
      up with `EEK_DATA_DIR` (prepared 2026-11-12; enabled at the cutover). Restore drill: see step 1.
- [ ] An offsite copy: every backup is on the same server today.

## Measuring voice chat use (Claude, next time)

Question: do players actually hear each other (not button presses, not their own microphone)?

- [ ] Client (`static/js/voice.js`), receiving side only: `getStats()` inbound-rtp `audioLevel` /
      `totalAudioEnergy` every ~10 s; "heard" = audible incoming sound while the page is in the foreground
      (`document.visibilityState`). Not the own microphone, no audio content, not who spoke.
- [ ] At the end of a game / on leaving, one summary to the server: heard others 0 / <1 / 1–5 / 5–15 / 15+ min.
- [ ] Server: `log_event("voice_heard", band)` in analytics.db (90 days), without names or room codes.
- [ ] `/admin/stats`: "players heard each other in X% of games", average listening time.
- [ ] Privacy notice ("Belső statisztika"): voice use is counted as whether audible sound arrived, without
      content.
- [ ] Tests: pytest (server) and node (voice.js), mutation check. Needs a production restart.

Note: the number of TURN connections (~1060 / 30 days) doesn't show use, because voice connects
automatically for everyone.

## AdSense (2026-10-16)

Details: [features/ads/privacy-notice-text.md](features/ads/privacy-notice-text.md),
[features/ads/plan.md](features/ads/plan.md).

Gergo:
- [x] Auto ads off, auto optimize off.
- [x] Consent message (CMP) published: Hungarian, Consent / Do not consent (all countries) / Options, logo.
- [x] `/ads.txt` live after a restart (2026-10-16).
- [ ] Waiting for Google's approval (review requested 2026-10-16). When it arrives, tell Claude; if
      rejected, bring the reason.

Claude, after the approval:
- [ ] Load the AdSense script only on `/`, `/join`, `/create` (`static/js/ads.js`, `ADS_ENABLED = true`).
- [ ] The bottom banner (320×50, slot 1343136630) in the ad slot box; 320×50 and centered on desktop too;
      must not overflow a 320 px wide phone.
- [ ] An advertising section in `adatkezeles.html` with an effective date.
- [ ] Withdrawing consent: check that Google's CMP "Privacy settings" link appears on the 3 pages; if not,
      an own footer link (`googlefc.showRevocationMessage()`).
- [ ] Update `tests/test_ad_banner.py`: Google code only on the 3 allowed pages.
- [ ] After going live: no ad-network request on `/game`, `/room`, `/kartyak` (check in a browser).

Done: the ad slot (off by default, `?ads=preview`), Umami in the privacy notice (2-year retention + daily
deletion timer), AdSense ownership meta tag and `/ads.txt`, the bottom banner unit, Umami path normalisation.

## Project hygiene (2026-11-06)

- [ ] Tidy `docs/`: delete `analitika.md` (1525 lines of Python saved as .md); outdated `PROJECT_STRUCTURE.md`
      and `PROGRESS.md` → an updated `architecture.md` and the history in `worklogs/`; finished plans and
      one-off notes → `worklogs/` or `docs/private/`. Overviews in English (portfolio); game text stays
      Hungarian.
- [ ] The live folder (`/opt`): archive and delete `cards.db`, `cards_safety_*.db`, `duplicate_cards.csv`, the
      empty `cron/`; move `backups/` out of the code folder. Together with the move to the container.
- [ ] Consider moving the open work to GitHub Issues (visible in the repo, industry practice).
