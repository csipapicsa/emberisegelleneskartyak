# Runbook: monitoring and alerts

How the game is monitored, and what to do when one of its alerts fires.

## How it fits together

```
game /metrics (prod 127.0.0.1:8010, staging :8011) ─▶ Prometheus (server-wide, every 30 s, 15 days)
                                                         ├─▶ Grafana: "EEK game" dashboard
                                                         └─▶ alert rules ─▶ Alertmanager ─▶ phone push (ntfy)
```

- The server runs one Prometheus for every project on it; the host, the containers and the public sites
  (probed from outside) are watched there. This repository owns only the game's part:
  - [`server/metrics.py`](../../server/metrics.py): what the game measures, served at `/metrics`;
  - [`deploy/monitoring/eek.rules.yml`](../../deploy/monitoring/eek.rules.yml): the game's alert rules, with
    promtool unit tests in `deploy/monitoring/tests/` (run in CI, `lint` job);
  - [`deploy/monitoring/make_dashboard.py`](../../deploy/monitoring/make_dashboard.py): generates the Grafana
    dashboard `eek-dashboard.json` (loaded from the file, so changes go through a PR).
- `/metrics` is only for Prometheus on the server: requests that came through nginx (they carry `X-Real-IP` /
  `X-Forwarded-For`) get a 404, and the nginx configs block the path as well.

```bash
curl -s 127.0.0.1:8010/metrics | grep ^eek_      # production
curl -s 127.0.0.1:8011/metrics | grep ^eek_      # staging
```

## What is measured

| Metric | Type | Meaning |
|---|---|---|
| `eek_http_requests_total{method, route, status}` | counter | requests by route **template** (`/api/room/{code}`), so the number of series stays bounded |
| `eek_http_request_duration_seconds{method, route}` | histogram | latency per route template |
| `eek_rooms{phase}` | gauge | rooms in memory: lobby, submitting, revealed, round_ending, finished |
| `eek_players{kind}` | gauge | humans connected / disconnected, computer players |
| `eek_websocket_connections` | gauge | open WebSockets |
| `eek_games_started_total` | counter | games started |
| `eek_computer_player_decisions_total{decision}` | counter | the computer player's picks: `model` or `random` (fallback) |
| `eek_model_requests_total{outcome}`, `eek_model_request_duration_seconds` | counter, histogram | calls to the model service as the game sees them |
| `eek_build_info{version}` | gauge | the running commit |
| `process_*`, `python_*` | | memory, CPU, open files, garbage collection of the game process |
| `eek_ml_train_last_run_success{result}`, `eek_ml_train_last_run_timestamp_seconds`, `eek_ml_train_last_success_timestamp_seconds` | gauge | how the last nightly training ended (`result`: systemd's `SERVICE_RESULT`); not from `/metrics`, see below |

The training metrics come from node_exporter's textfile collector: after every run, `eek-ml-train.service`
writes `/var/lib/node_exporter/textfile/eek_ml_train*.prom` (`scripts/record_unit_result.sh`).

Rooms, players, sockets and the computer player's counts are read when Prometheus scrapes, not counted
separately, so they can't drift from the real state. Counters start from zero when the container restarts
(every release); Prometheus' `rate()` and `increase()` handle that.

## Alerts

Every alert names its environment (`prod` or `staging`) in the notification.

### EEKBotFallingBack

Over 20% of the computer player's decisions in the last hour were random (and at least 10 decisions): the
model service didn't answer in time, so the computer player played without the model.

1. Is the model service up and serving both models?
   `curl -s 127.0.0.1:8021/healthz` (expects `challenger` and `champion`).
2. Its log: `journalctl CONTAINER_NAME=eek-model -n 50`. The game's side: `journalctl CONTAINER_NAME=eek-prod |
   grep "model service"` (timeouts or errors per call).
3. Dashboard *Model service latency and errors*: slow (the server is busy: check the server overview) or failing
   (errors)? A slow model service usually means CPU contention, e.g. a training run at the wrong priority.
4. Restart it: `systemctl --user -M eek@ restart eek-model`. A broken new model: `python -m ml.promote --rollback`
   (see [../ml/pipeline.md](../ml/pipeline.md#promoting-a-model)).

### EEKServerErrors

Over 5% of requests failed with a 5xx for 5 minutes (with at least 20 requests in 10 minutes).

1. Which routes: dashboard *Responses by status*, or
   `curl -s 127.0.0.1:8010/metrics | grep 'status="5'`.
2. The tracebacks: `journalctl CONTAINER_NAME=eek-prod -p warning -n 100`.
3. Just after a release? Roll back: Promote the previous commit ([deploy.md](deploy.md), "Roll back code").
4. A database problem (locked, disk full): check the disk, then `/healthz`.

### EEKSlowRequests

95% of requests took longer than 1 s for 15 minutes (printing and `/metrics` not counted; at least 20
requests in 10 minutes).

1. Which routes: dashboard *Slowest routes (p95)*.
2. Is the whole server slow? Server overview: CPU by project, memory, swap. The game shares 3 cores with the
   radio's text-to-speech and the nightly training.
3. The game process itself: dashboard *Game process CPU / memory*. Many rooms at once? A WebSocket load test
   (TODO) will tell where the limit is.

### EEKTrainingFailed

The last nightly training run (04:00) failed. The alert names systemd's result: `exit-code` (the pipeline
stopped: a failed validation check, MLflow down, an exception), `timeout` (over 1 hour), `oom-kill` (over its
3 GB), `signal`. It stays on until the next run succeeds. The game isn't affected: the computer player keeps
the current champion.

1. Why: `journalctl -u eek-ml-train -n 30`. A failed check prints the snapshot's `validation.json` path; its
   `examples` name the game, round and seat.
2. A failed check is usually a check that is too strict for a real game situation, not bad data (PR #41,
   PR #67): look at those rows, fix the check with a test, and add the situation to
   `tests/ml/test_validate.py`.
3. Run it again after the fix, without waiting for the night: `systemctl start eek-ml-train` (as root).

### EEKTrainingMissing

No training run has ended for over 26 hours: the timer is off or the run hangs.

1. `systemctl list-timers eek-ml-train.timer`: enabled, and when is the next run?
2. `systemctl status eek-ml-train`: still running (stuck)? The unit's 1 hour timeout should end it.
3. Re-enable: `systemctl enable --now eek-ml-train.timer` (as root).
