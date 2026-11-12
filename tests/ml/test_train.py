"""ml/train.py: one training cycle on sample data, with the stand-in encoder and a throwaway MLflow store."""
from __future__ import annotations

import json

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
    assert names == ["baseline random", "baseline popularity", "linear minilm", "mlp minilm",
                     "linear minilm [B]", "mlp minilm [B]", "linear minilm [C]", "mlp minilm [C]"]
    parent = mlflow.get_run(cycle["parent_run_id"])
    assert parent.data.tags["kind"] == "cycle"
    assert parent.info.run_name.startswith("cycle 20") and parent.info.run_name.endswith(f"· data to {snapshot.name}")
    assert parent.data.tags["snapshot"] == snapshot.name
    assert parent.data.tags["image"] == "ghcr.io/example/eek-ml-train:sha-abc1234"
    assert parent.data.tags["cloud_run_execution"] == "eek-train-abc12"
    assert int(parent.data.params["n_test_games"]) >= 1
    assert parent.inputs.dataset_inputs[0].dataset.name == snapshot.name
    children = mlflow.search_runs(experiment_names=["train-test"], output_format="list",
                                  filter_string=f"tags.mlflow.parentRunId = '{cycle['parent_run_id']}'")
    assert len(children) == 8  # 2 baselines + 2 structures x 3 data variants
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


def test_challenger_loads_and_returns_raw_scores(cycle):
    model = mlflow.pyfunc.load_model(f"models:/{train.REGISTERED_MODEL}@challenger")
    inputs = model.metadata.signature.inputs.inputs[0]
    assert tuple(inputs.shape) == (-1, 11, 384)
    x = np.random.default_rng(0).random((4, 11, 384), dtype=np.float32)
    scores = np.asarray(model.predict(x))
    assert scores.shape == (4, 10)
    assert not np.allclose(scores.sum(axis=1), 1, atol=1e-3), "scores, not probabilities"
    # Each card's score depends only on the black card and that card, so a short hand can be padded.
    swapped = x.copy()
    swapped[:, 5:] = x[:, 1:2]
    assert np.allclose(np.asarray(model.predict(swapped))[:, :4], scores[:, :4], atol=1e-5)


def test_challenger_carries_the_new_hand_cutoffs(cycle):
    version = mlflow.MlflowClient().get_model_version_by_alias(train.REGISTERED_MODEL, "challenger")
    assert version.tags["output"] == "scores"
    cutoffs = json.loads(version.tags["best_score_percentiles"])
    assert list(cutoffs) == [str(q) for q in train.REFRESH_PERCENTILES]
    assert list(cutoffs.values()) == sorted(cutoffs.values()), "higher percentile, higher cut-off"
    run = mlflow.get_run(cycle["challenger"]["run_id"])
    assert run.data.metrics["train_best_score_p10"] == pytest.approx(cutoffs["10"])


def test_other_variants_are_compared_with_A_and_not_logged_as_models(cycle):
    by_name = {r["name"]: r for r in cycle["runs"]}
    for name in ("linear minilm [B]", "mlp minilm [C]"):
        run = mlflow.get_run(by_name[name]["run_id"])
        assert run.data.tags["data_variant"] == name[-2]
        for split in ("val", "test"):
            lo, delta, hi = (run.data.metrics[f"{split}_mrr_vs_A_{k}"] for k in ("lo", "delta", "hi"))
            assert lo <= delta <= hi
        assert "model_uri" not in by_name[name], "only the registered variant logs a model"
    a = mlflow.get_run(by_name["linear minilm"]["run_id"])
    assert a.data.tags["data_variant"] == "A" and "model_uri" in by_name["linear minilm"]
    parent = mlflow.get_run(cycle["parent_run_id"])
    assert int(parent.data.params["n_train_vote_examples_C"]) > 0, "sample games have decided vote rounds"
    # This sample (seed 5) has no game with a computer player, so B trains on the same picks as A;
    # test_evaluate.py covers B with explicit bot games.
    assert int(parent.data.params["n_train_bot_game_picks_A"]) == 0
    assert int(parent.data.params["n_train_examples_B"]) == int(parent.data.params["n_train_examples_A"])
    assert int(parent.data.params["n_human_picks"]) >= sum(int(parent.data.params[f"n_{s}_picks"])
                                                           for s in ("train", "val", "test"))


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


def test_short_hands_are_padded_and_masked(snapshot, tmp_path):
    torch = pytest.importorskip("torch")
    short = evaluate.Example(1, 1, -1, 1, (2, 3, 4), 2, source="vote")
    full = evaluate.Example(1, 1, 0, 1, tuple(range(2, 12)), 0)
    vectors = np.random.default_rng(0).random((12, 384), dtype=np.float32)
    row = {i: i for i in range(12)}
    x, y = train.to_tensors([short, full], row, vectors)
    mask = train.hand_mask([short, full])
    assert x.shape == (2, 11, 384) and mask[0].tolist() == [True] * 3 + [False] * 7 and mask[1].all()
    assert train.hand_mask([full]) is None
    config = train.TrainConfig(lr=0.05, weight_decay=0, max_epochs=30, patience=5)
    model, _, _ = train.fit((x, y, mask), (x, y, mask), 384, config)
    probs = train.predict_probs(model, x, mask)
    assert np.allclose(probs[0, 3:], 0) and np.allclose(probs.sum(axis=1), 1)
    assert torch.isfinite(torch.tensor(probs)).all()
