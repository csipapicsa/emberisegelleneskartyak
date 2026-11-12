"""ml/train.py: one training cycle on sample data, with the stand-in encoder and a throwaway MLflow store."""
from __future__ import annotations

import mlflow
import numpy as np
import pytest

from ml import evaluate, export, features, train, validate
from scripts import make_sample_gameplay_db as sample

from .conftest import StemEncoder


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "gameplay.db"
    sample.generate(db, games=24, seed=5)
    folder, _ = export.export(db, tmp_path_factory.mktemp("snapshots"))
    assert all(r.ok for r in validate.validate_snapshot(folder, first_day=sample.FIRST_DAY))
    return folder


@pytest.fixture(scope="module")
def cycle(snapshot, tmp_path_factory):
    """One cycle (one embedding, both structures), shared by the tests below."""
    mp = pytest.MonkeyPatch()
    mp.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    mp.setenv("EEK_ML_IMAGE", "ghcr.io/example/eek-ml-train:sha-abc1234")  # as inside the training image
    mp.setenv("CLOUD_RUN_EXECUTION", "eek-train-abc12")  # as in a Cloud Run Job
    mlflow.set_tracking_uri((tmp_path_factory.mktemp("mlflow") / "mlruns").as_uri())
    try:
        yield train.run_cycle(snapshot, embeddings=["minilm"], experiment="train-test",
                              cache_dir=tmp_path_factory.mktemp("emb"), encoder_factory=StemEncoder)
    finally:
        mlflow.set_tracking_uri(None)
        mp.undo()


def test_cycle_logs_a_parent_with_baselines_and_models(cycle, snapshot):
    names = [r["name"] for r in cycle["runs"]]
    assert names == ["baseline random", "baseline popularity", "linear minilm", "mlp minilm"]
    parent = mlflow.get_run(cycle["parent_run_id"])
    assert parent.data.tags["kind"] == "cycle"
    assert parent.data.tags["snapshot"] == snapshot.name
    assert parent.data.tags["image"] == "ghcr.io/example/eek-ml-train:sha-abc1234"
    assert parent.data.tags["cloud_run_execution"] == "eek-train-abc12"
    assert int(parent.data.params["n_test_games"]) >= 1
    assert parent.inputs.dataset_inputs[0].dataset.name == snapshot.name
    children = mlflow.search_runs(experiment_names=["train-test"], output_format="list",
                                  filter_string=f"tags.mlflow.parentRunId = '{cycle['parent_run_id']}'")
    assert len(children) == 4
    model_run = mlflow.get_run(next(r["run_id"] for r in cycle["runs"] if r["name"] == "mlp minilm"))
    assert model_run.data.params["embedding_revision"] == features.EMBEDDING_MODELS["minilm"].revision
    for split in evaluate.SPLITS:
        assert f"{split}_mrr" in model_run.data.metrics
    curve = mlflow.MlflowClient().get_metric_history(model_run.info.run_id, "curve_val_loss")
    assert len(curve) == int(model_run.data.params["epochs_run"]) >= int(model_run.data.params["best_epoch"])


def test_baselines_and_models_on_the_planted_signal(cycle):
    test = {r["name"]: r["test"] for r in cycle["runs"]}
    assert test["baseline random"]["mrr"] == pytest.approx(0.2929, abs=1e-3)
    assert test["baseline popularity"]["mrr"] > 0.32, "sample players prefer high-quality cards"
    for name in ("linear minilm", "mlp minilm"):
        assert test[name]["mrr"] > 0.32 and test[name]["top1"] > 0.15


def test_best_validation_model_becomes_the_challenger(cycle):
    trained = [r for r in cycle["runs"] if "model_type" in r]
    best = max(trained, key=lambda r: (r["val"]["mrr"], -r["val"]["log_loss"]))
    assert cycle["challenger"]["run_id"] == best["run_id"]
    version = mlflow.MlflowClient().get_model_version_by_alias(train.REGISTERED_MODEL, "challenger")
    assert version.version == cycle["challenger"]["version"]
    assert version.tags["embedding"] == "minilm"


def test_challenger_loads_and_returns_probabilities(cycle):
    model = mlflow.pyfunc.load_model(f"models:/{train.REGISTERED_MODEL}@challenger")
    inputs = model.metadata.signature.inputs.inputs[0]
    assert tuple(inputs.shape) == (-1, 11, 384)
    probs = np.asarray(model.predict(np.random.default_rng(0).random((4, 11, 384), dtype=np.float32)))
    assert probs.shape == (4, 10) and np.allclose(probs.sum(axis=1), 1, atol=1e-5)


def test_fit_is_reproducible_and_stops_early(snapshot, tmp_path):
    _, tables = export.read_snapshot(snapshot)
    splits = evaluate.split_by_game(evaluate.pick_examples(tables), tables["games"])
    ids, vectors = features.embed({c["text_id"]: c["text"] for c in tables["card_texts"]},
                                  features.EMBEDDING_MODELS["minilm"], tmp_path, StemEncoder)
    row = {int(t): i for i, t in enumerate(ids)}
    data = {s: train.to_tensors(splits[s], row, vectors) for s in ("train", "val")}
    config = train.TrainConfig(lr=0.05, weight_decay=0, max_epochs=400, patience=5)
    first, curve, best_epoch = train.fit(data["train"], data["val"], 384, config)
    second, _, _ = train.fit(data["train"], data["val"], 384, config)
    assert len(curve) < 400 and len(curve) - best_epoch == 5
    for a, b in zip(first.state_dict().values(), second.state_dict().values(), strict=True):
        assert np.array_equal(a.numpy(), b.numpy())


def test_no_register(snapshot, tmp_path, mlflow_dir):
    result = train.run_cycle(snapshot, embeddings=["e5"], model_types=["linear"], register=False,
                             cache_dir=tmp_path, encoder_factory=StemEncoder)
    assert result["challenger"] is None
    assert mlflow.MlflowClient().search_registered_models() == []


def test_main_argument_errors(tmp_path, capsys):
    assert train.main(["--models", "transformer"]) == 2
    assert "transformer" in capsys.readouterr().err
    assert train.main(["--snapshot", str(tmp_path / "nope"), "--embeddings", "e5"]) == 1
    assert "Training failed" in capsys.readouterr().err


def test_linear_cap_doubled_for_slow_learners():
    # linear e5 was still improving at 300 epochs in every cycle up to 2026-11-11; early stopping ends the rest.
    assert train.CONFIGS["linear"].max_epochs == 600
    assert train.CONFIGS["mlp"].max_epochs == 300, "the MLP always stopped early (best epoch < 60)"
