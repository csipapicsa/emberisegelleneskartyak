# Running the ML pipeline

The steps so far, run on the server from the repository root. Plan and status:
[implementation-plan.md](implementation-plan.md).

## Setup (once)

The pipeline has its own virtualenv, separate from the game's `venv/` (which production runs from):

```bash
python3.11 -m venv venv-ml
venv-ml/bin/pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements-dev.txt -r requirements-ml.txt
```

PyTorch comes from its CPU-only index: the PyPI build bundles GPU libraries worth several GB.
MLflow's address and login come from `MLFLOW_TRACKING_URI` (in `~/.bashrc`) and `~/.mlflow/credentials`
([deploy/containers/README.md](../../deploy/containers/README.md#mlflow)).

## Steps

```bash
venv-ml/bin/python -m ml.export                 # gameplay.db -> ml-data/snapshots/<day>_<hash>/
venv-ml/bin/python -m ml.validate ml-data/snapshots/<day>_<hash>
venv-ml/bin/python -m ml.embedding_benchmark    # latest snapshot, MLflow experiment "embedding-benchmark"
venv-ml/bin/python -m ml.train                  # latest snapshot, MLflow experiment "pick-model"
```

| Step | Reads | Writes |
|---|---|---|
| `ml.export` | `gameplay.db` (a consistent copy) | Parquet tables + `manifest.json` with the content hash; nothing if the data is unchanged |
| `ml.validate` | a snapshot | `validation.json`; exit 1 if any of the 13 checks fails |
| `ml.embedding_benchmark` | a validated snapshot | one MLflow run per embedding model; the embeddings cache in `ml-data/embeddings/` |
| `ml.train` | a validated snapshot, the embeddings | a parent run with 2 baselines + 6 models nested; the best on validation registered as `pick-model@challenger` |

Every ML process lowers its own CPU priority to the minimum (`ml.lower_priority()`: SCHED_IDLE, nice 19),
so the live game always comes first.

## Sample data

Everything runs on made-up games too, which is what the tests and CI use:

```bash
venv-ml/bin/python scripts/make_sample_gameplay_db.py
venv-ml/bin/python -m ml.export --db sample-data/gameplay.db --out /tmp/snapshots
venv-ml/bin/python -m ml.validate --first-day 2025-01-01 /tmp/snapshots/<name>
```

The tests (`venv-ml/bin/python -m pytest tests/ml`) use a small stand-in encoder instead of the real
embedding models and a throwaway local MLflow store, so they need no download and no server.
