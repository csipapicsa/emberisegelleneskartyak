# ML implementation plan

The working checklist behind [roadmap.md](roadmap.md): concrete tasks, who does what, and how each step is
checked. Work happens in small PRs; every milestone ends with its "done when" check.

**Who:** *Gergo* does what needs sudo, DNS or a cloud console login; *Claude* writes the code, the PRs and
the commands to run.

## Skills this covers

The requirements that come up most in ML Engineer job ads, and where each one is practised:

| Skill | Milestone |
|---|---|
| Docker, container deployment, rollback | 0, 4 |
| Experiment tracking, model registry (MLflow) | 1, 2 |
| Reproducible training: data versioning, code and environment pinning | 2 |
| CI/CD for ML: tested pipeline code and training image | 2 |
| Cloud training jobs, service accounts, IAM, object storage (GCP) | 3 |
| Model serving behind a REST API (FastAPI) | 4 |
| Evaluation gate, controlled promotion, rollback | 4 |
| Model monitoring and data drift | 5 |

---

## Milestone 0: production into the container

Prerequisite: it closes the last security gap (the game runs as an admin account) and moves all data out of
the code folder. Best done in the morning, when few people play.

| # | Task | Who |
|---|---|---|
| 0.1 | `deploy/containers/eek-prod.container`: like staging, image `:prod`, port 127.0.0.1:8010, data `/srv/eek/prod`, env `/etc/eek/prod.env`; **no** auto-update | Claude |
| 0.2 | Promote workflow (`.github/workflows/promote.yml`, manual trigger): retags a tested `sha-<commit>` image as `:prod` in GHCR; rollback = promote the previous commit | Claude |
| 0.3 | `/etc/eek/prod.env` from `/etc/emberisegellenes/privacy.env`. **Keep the same `EEK_PRIVACY_HMAC_SECRET`**, otherwise the stored card-vote hashes stop matching | Gergo (commands from Claude) |
| 0.4 | Cutover: stop the old service → copy the databases with SQLite's backup API into `/srv/eek/prod` (same layout) → start the container → check `/healthz` and play a round. nginx stays as it is (same port) | Gergo |
| 0.5 | Backup and print-cleanup timers run as `eek` with `EEK_DATA_DIR=/srv/eek/prod`; backups go to `/srv/eek/backups`, outside the code folder | Claude + Gergo |
| 0.6 | The old service stays installed but disabled, as a fallback for a week | Gergo |
| 0.7 | Rollback drill: promote the previous commit, check `/healthz`, promote back | Gergo + Claude |
| 0.8 | Restore drill: restore last night's backup into a temp folder, `PRAGMA integrity_check`, start the app on it | Claude |
| 0.9 | Clean up `/opt`: archive and delete `cards.db`, `cards_safety_*.db`, `duplicate_cards.csv`, the old `backups/`, the empty `cron/`. Card database uploads now go to `/srv/eek/prod/database/` | Gergo |
| 0.10 | `docs/runbooks/deploy.md`: merge → staging → promote → rollback, with the commands | Claude |

**Done when:** production runs as `eek` from the `:prod` image, `/healthz` shows the commit, both drills are
written down, and `/opt/emberisegellenes_kartyak` holds only code.

## Milestone 1: MLflow server

| # | Task | Who |
|---|---|---|
| 1.1 | DNS A record `mlflow.emberisegelleneskartyak.hu` → the server | Gergo |
| 1.2 | `deploy/containers/eek-mlflow.container`: the official MLflow image (pinned version), backend store SQLite in `/srv/eek/mlflow/mlflow.db`, artifacts served by MLflow (`--serve-artifacts`) from `/srv/eek/mlflow/artifacts`, port 127.0.0.1:5000 | Claude |
| 1.3 | Login: MLflow's built-in basic-auth app, with users `admin`, `trainer` (pipelines and the cloud job) and `reader`; nginx in front with HTTPS (certbot) | Claude + Gergo |
| 1.4 | The daily backup includes `mlflow.db`; artifacts are covered by a size check (models are small) | Claude |

**Done when:** a test run from a laptop (`MLFLOW_TRACKING_URI=https://mlflow...`) shows up in the UI with a
metric and an artifact.

**Notes from building it (2026-11-07):** the official image lacks `Flask-WTF`, which the login needs, so
`deploy/mlflow/` builds a small image on top of it, tested and published by its own workflow. MLflow also needs
`MLFLOW_FLASK_SERVER_SECRET_KEY`, refuses admin passwords under 12 characters, and starts seven background job
workers by default (about 1.5 GB of RAM, for GenAI features), which the image switches off. Artifacts aren't in
the daily backup: a model can be rebuilt from its run's commit, data snapshot and image.

## Milestone 2: the pipeline, run locally

**Layout:** a separate `ml/` package with its own lock files (`requirements-ml.in/.txt`, PyTorch CPU build),
so the game image stays small.

| # | Task | Who |
|---|---|---|
| 2.1 | ✅ `scripts/make_sample_gameplay_db.py`: synthetic games in the real schema, so tests and CI can run without real data | Claude |
| 2.2 | `ml/export.py`: snapshot `gameplay.db` into Parquet (rounds, decisions, votes, card texts) plus a manifest with row counts and a content hash; human players only | Claude |
| 2.3 | `ml/validate.py`: the consistency checks run on 2026-11-06 (every ID resolves, hand sizes, picks in hand, czar rules); the pipeline stops on any failure | Claude |
| 2.4 | `ml/features.py`: card embeddings with the three embedding models (below), cached per (model, text ID); shipped with the snapshot | Claude |
| 2.4b | `ml/embedding_benchmark.py`: the first MLflow experiment, no game data needed: per embedding model, load time, cards per second and memory on this server's CPU | Claude |
| 2.5 | `ml/train.py`: baselines (random, smoothed card popularity) and the model grid (below): pick from hand as a softmax over the hand | Claude |
| 2.6 | `ml/evaluate.py`: split by time and by game, top-1, top-3, MRR, log loss, always next to the popularity baseline | Claude |
| 2.7 | MLflow logging: parameters, metrics, the dataset (`mlflow.data`, with its hash), git commit, image tag, and the model as a pyfunc with an input/output signature; register the best run as `challenger` | Claude |
| 2.8 | `ml/pipeline.py`: one command runs every step from a config file. Retraining trigger: a daily timer that runs the grid only if enough new rounds arrived since the last run (most games are at weekends) | Claude |
| 2.9 | `Dockerfile.ml` and a CI job: pipeline unit tests and a tiny training run on the sample data; the training image is published to GHCR on `main` | Claude |

**The model grid (decided 2026-11-07):** 3 embedding models × 2 model structures = 6 runs per training cycle,
plus the two baselines, all in one MLflow experiment so they can be compared over time.

| Embedding model | Size | Why |
|---|---|---|
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 118M parameters, 384 dims | the classic baseline |
| `intfloat/multilingual-e5-small` | 118M, 384 dims | contrastively trained for relevance; inputs need the `query: ` prefix |
| `ibm-granite/granite-embedding-97m-multilingual-r2` | 97M, 384 dims | 2026 ModernBERT model, Hungarian among its supported languages |

All three are small on purpose (CPU only, about 1.1 GB of downloads together) and give the same 384 dimensions,
so the two structures run on any of them unchanged:

- **A, linear scorer:** logistic-regression style, on black/white pair features (the two embeddings, their
  product and difference); softmax over the hand.
- **B, small MLP** (PyTorch) on the same inputs.

The data is small, so differences between the six will be noisy. That's accepted: the point is to follow
how the models behave over time in MLflow, not to build a strong player.

**Done when:** two runs (baseline and model) are compared in MLflow, and a run can be rebuilt from its
commit, data hash and image tag.

## Milestone 3: the same training in GCP

| # | Task | Who |
|---|---|---|
| 3.1 | GCP project, billing (free trial), a budget alert at a few euros; enable Cloud Run, Cloud Storage, Artifact Registry and Secret Manager | Gergo |
| 3.2 | Bucket for snapshots; service accounts: `uploader` (may only write to the bucket; its key lives on the server, root:eek 0640) and `trainer` (the job's identity: reads the bucket and the MLflow secret) | Gergo, commands from Claude |
| 3.3 | Training image in Artifact Registry (pushed by CI, or mirrored from GHCR) | Claude |
| 3.4 | Cloud Run Job: runs the pipeline on a snapshot URI; the `trainer` MLflow password comes from Secret Manager | Claude + Gergo |
| 3.5 | `scripts/train_in_cloud.sh` on the server: export → upload → start the job → print the MLflow run link | Claude |

**Done when:** a cloud run appears in MLflow next to the local ones and gives the same metrics on the same
snapshot.

## Milestone 4: serving, the computer player, promotion

| # | Task | Who |
|---|---|---|
| 4.1 | `ml_service/`: a small FastAPI app that loads the model with the `champion` alias from MLflow, polls the alias every few minutes, `POST /predict` (black card and hand → probability per card), `/healthz` with the model version | Claude |
| 4.2 | `deploy/containers/eek-model.container`; staging uses it first | Claude + Gergo |
| 4.3 | Game: the room admin can add a computer player; it asks the service with a short timeout and picks at random if there's no answer; the game log stores its model version and probabilities; training excludes it | Claude |
| 4.4 | `ml promote`: compares `challenger` with `champion` on the same test set, prints the result, and moves the alias only after confirmation; `ml rollback` moves it back | Claude |

**Done when:** you play against the bot on staging, promote a new model, and roll it back, all visible in
MLflow.

## Milestone 5: monitoring

| # | Task | Who |
|---|---|---|
| 5.1 | A weekly job (systemd timer): the bot's vote share against humans, the offline metric on the newest week, and the share of card texts the model hasn't seen; logged to an MLflow `monitoring` experiment | Claude |
| 5.2 | A short section in the runbook: what to look at, and when to retrain | Claude |

**Done when:** MLflow shows a weekly chart of the bot's performance.

---

## Suggested first session

1. Milestone 0, tasks 0.1–0.6 in the morning (with Gergo at the terminal for the sudo steps), then the two
   drills.
2. Milestone 1 (needs the DNS record first).
3. Start milestone 2 with 2.1–2.3. Real data keeps accumulating meanwhile; a few hundred rounds are enough for
   the first training run.
