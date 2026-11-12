# Experiment notebooks

Model ideas the training cycle (`ml/train.py`) doesn't have yet, one notebook per model family. They share
`ml/experiments.py`: same snapshot split, metrics, baselines and training loop (`train.train_loop`, early
stopping included) as the cycle, so the numbers compare directly. Pick by **validation** MRR; test is only
reported. An idea that wins moves into `ml/train.py`.

| Notebook | Question |
|---|---|
| `01_bilinear` | Does a low-rank black × white interaction add anything over card quality alone (rank 0)? |
| `02_popularity_residual` | Popularity as an input, the model learns only what it misses. |
| `03_filled_sentence` | Embed the black card with the answer in its blank, as one sentence. |
| `04_other_encoder` | Same structures as the pipeline, a different encoder (default: multilingual-e5-base). |

## Setup (once)

```bash
venv-ml/bin/pip install --require-hashes -r requirements-notebooks.txt
```

In VS Code, pick `venv-ml/bin/python` as the notebook kernel (needs the Jupyter extension).

## Run in the background

```bash
venv-ml/bin/python -m ml.notebooks list
venv-ml/bin/python -m ml.notebooks run 01                       # by number or name
venv-ml/bin/python -m ml.notebooks run all -p embedding=granite # override the parameters cell
nohup venv-ml/bin/python -m ml.notebooks run all > ml-data/notebook-runs/all.log 2>&1 &
```

The executed copy, with all outputs, lands in `ml-data/notebook-runs/<time>_<name>.ipynb`. Results go to
MLflow, experiment `pick-model-experiments` (never `pick-model`: the nightly pipeline reads its state from
that one). Everything runs on idle CPU only, like the nightly training.

## Rules

- Commit notebooks **without outputs** (a test checks it). Outputs belong in the executed copies and MLflow.
- Each notebook has one cell tagged `parameters` (papermill overrides it) with `snapshot` and `log_mlflow`.
- New notebook: next number, `ex.log("<its name>", ...)` at the end.
