"""ml/experiments.py: the notebooks' shared helpers, on sample data with the stand-in encoder."""
from __future__ import annotations

import mlflow
import numpy as np
import pytest

from ml import evaluate, export, features, train, validate
from ml import experiments as ex
from ml.evaluate import Example
from scripts import make_sample_gameplay_db as sample

from .conftest import StemEncoder

torch = pytest.importorskip("torch")


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "gameplay.db"
    sample.generate(db, games=24, seed=5)
    folder, _ = export.export(db, tmp_path_factory.mktemp("snapshots"))
    assert all(r.ok for r in validate.validate_snapshot(folder, first_day=sample.FIRST_DAY))
    return ex.load(folder)


@pytest.mark.parametrize(("black", "white", "filled"), [
    ("Mi ragad? ________.", "A méz.", "Mi ragad? A méz."),
    ("A ____ a legjobb barátom.", "Egy kecske", "A Egy kecske a legjobb barátom."),
    ("Mitől ragadok?", "A méztől.", "Mitől ragadok? A méztől."),
    ("Ez volt ____", "Bumm...", "Ez volt Bumm..."),
])
def test_fill_blank(black, white, filled):
    assert ex.fill_blank(black, white) == filled


def test_load_splits_like_the_training_cycle(data):
    _, tables = export.read_snapshot(data.snapshot)
    expected = evaluate.split_by_game(evaluate.pick_examples(tables), tables["games"])
    assert data.splits == expected
    assert data.sizes()["n_train_picks"] == len(expected["train"])
    assert np.array_equal(data.targets["test"], [e.target for e in expected["test"]])


def test_tensors_match_the_training_cycle(data, tmp_path):
    inputs, targets = data.tensors("minilm", cache_dir=tmp_path, encoder_factory=StemEncoder)
    ids, vectors = features.embed(data.texts, features.EMBEDDING_MODELS["minilm"], tmp_path, StemEncoder)
    row = {int(t): i for i, t in enumerate(ids)}
    for s in evaluate.SPLITS:
        x, y = train.to_tensors(data.splits[s], row, vectors)
        assert torch.equal(inputs[s][0], x) and torch.equal(targets[s], y)


def test_tensors_with_a_different_model_for_the_hand(data, tmp_path):
    e5 = features.EMBEDDING_MODELS["e5"]
    passage = features.EmbeddingModel("e5", e5.hf_id, e5.revision, prefix="passage: ")
    inputs, _ = data.tensors(e5, passage, cache_dir=tmp_path, encoder_factory=StemEncoder)
    assert inputs["train"][0].shape[1:] == (11, 384)
    with pytest.raises(ValueError, match="differ in size"):
        data.tensors(e5, features.EmbeddingModel("big", "x/y", "abc", dim=768), cache_dir=tmp_path,
                     encoder_factory=StemEncoder)


def test_filled_tensors_embed_each_pair_once_and_resume(data, tmp_path, stem_encoders):
    inputs, targets = data.filled_tensors("minilm", tmp_path, stem_encoders, chunk=500)
    pairs = {(e.black, c) for split in data.splits.values() for e in split for c in e.hand}
    encoded = [t for enc in stem_encoders.made for call in enc.calls for t in call]
    assert len(encoded) == len(pairs) and len(stem_encoders.made) > 1, "one encoder per chunk"
    e = data.splits["val"][0]
    expected = StemEncoder(features.EMBEDDING_MODELS["minilm"]).encode(
        [ex.fill_blank(data.texts[e.black], data.texts[e.hand[3]])])[0]
    assert np.allclose(inputs["val"][0][0, 3].numpy(), expected)
    assert inputs["test"][0].shape == (len(data.splits["test"]), 10, 384)
    assert torch.equal(targets["test"], torch.tensor(data.targets["test"]))
    stem_encoders.made.clear()
    data.filled_tensors("minilm", tmp_path, stem_encoders, chunk=500)
    assert stem_encoders.made == [], "everything comes from the cache the second time"


def test_popularity_log_rates_reproduce_the_baseline(data):
    train_ex, test_ex = data.splits["train"], data.splits["test"]
    log_rates = ex.popularity_log_rates(train_ex, test_ex)
    probs = np.exp(log_rates) / np.exp(log_rates).sum(axis=1, keepdims=True)
    assert np.allclose(probs, evaluate.popularity_probs(train_ex, test_ex))


def test_popularity_leave_one_out_leaves_out_the_own_pick():
    hand = tuple(range(10))
    picks = [Example(1, 1, 0, 100, hand, 0), Example(1, 2, 0, 100, hand, 0), Example(1, 3, 0, 100, hand, 1)]
    plain = ex.popularity_log_rates(picks, picks, strength=10)
    loo = ex.popularity_log_rates(picks, picks, strength=10, leave_one_out=True)
    assert plain[0, 0] == pytest.approx(np.log((2 + 1) / (3 + 10)))
    assert loo[0, 0] == pytest.approx(np.log((1 + 1) / (2 + 10)))   # its own pick of card 0 left out
    assert loo[0, 1] == pytest.approx(np.log((1 + 1) / (2 + 10)))   # card 1's pick was another example's
    assert loo[2, 1] == pytest.approx(np.log((0 + 1) / (2 + 10)))
    single = [Example(1, 1, 0, 100, hand, 0)]
    assert np.allclose(ex.popularity_log_rates(single, single, leave_one_out=True), np.log(0.1)), "no data: prior"


def test_center_subtracts_the_training_mean():
    inputs = {s: (torch.randn(5, 11, 4) + 3, torch.ones(5, 10)) for s in evaluate.SPLITS}
    centered = ex.center(inputs)
    assert torch.allclose(centered["train"][0].reshape(-1, 4).mean(0), torch.zeros(4), atol=1e-5)
    mean = inputs["train"][0].reshape(-1, 4).mean(0)
    assert torch.allclose(centered["test"][0], inputs["test"][0] - mean)
    assert centered["val"][1] is inputs["val"][1], "only the embeddings are centered"


class TwoInputs(torch.nn.Module):
    """Embeddings plus an extra per-card input, like the popularity notebooks."""

    def __init__(self, dim):
        super().__init__()
        self.white = torch.nn.Linear(dim, 1)
        self.scale = torch.nn.Parameter(torch.ones(()))

    def forward(self, x, extra):
        return self.white(x[:, 1:]).squeeze(-1) + self.scale * extra


def test_fit_trains_any_module_reproducibly(data, tmp_path):
    inputs, targets = data.tensors("minilm", cache_dir=tmp_path, encoder_factory=StemEncoder)
    log_pop = {s: torch.tensor(ex.popularity_log_rates(data.splits["train"], data.splits[s]), dtype=torch.float32)
               for s in evaluate.SPLITS}
    inputs = {s: (inputs[s][0], log_pop[s]) for s in evaluate.SPLITS}
    config = train.TrainConfig(lr=0.05, weight_decay=0, max_epochs=200, patience=5)
    first = ex.fit("two", lambda: TwoInputs(384), inputs, targets, config, {"model_type": "two"})
    second = ex.fit("two", lambda: TwoInputs(384), inputs, targets, config)
    assert first.scores == second.scores
    assert first.params["model_type"] == "two" and first.params["parameters"] == 386
    assert first.params["epochs_run"] == len(first.curve) and first.params["max_epochs"] == 200
    assert first.scores["test"]["mrr"] > 0.32, "sample players prefer high-quality cards"


def test_fit_warns_when_still_improving_at_the_cap(data, tmp_path):
    inputs, targets = data.tensors("minilm", cache_dir=tmp_path, encoder_factory=StemEncoder)
    config = train.TrainConfig(lr=1e-4, weight_decay=0, max_epochs=3)
    with pytest.warns(UserWarning, match="max_epochs=3"):
        ex.fit("slow", lambda: train.make_scorer(384, config), inputs, targets, config)


def test_table_orders_by_validation_and_compares_with_popularity(data):
    results = ex.baselines(data)
    df = ex.table(results)
    assert list(df["val MRR"]) == sorted(df["val MRR"], reverse=True)
    assert df.loc["baseline popularity", "test MRR - popularity"] == 0
    assert df.loc["baseline random", "test MRR"] == pytest.approx(0.2929, abs=1e-3)


def test_log_writes_a_parent_and_one_run_per_result(data, tmp_path, mlflow_dir):
    inputs, targets = data.tensors("minilm", cache_dir=tmp_path, encoder_factory=StemEncoder)
    config = train.TrainConfig(lr=0.05, weight_decay=0, max_epochs=20, patience=5)
    results = ex.baselines(data) + [ex.fit("linear", lambda: train.make_scorer(384, config), inputs, targets, config)]
    parent_id = ex.log("99_test", data, results, {"embedding": "minilm"}, experiment="exp-test")
    parent = mlflow.get_run(parent_id)
    assert parent.data.tags["kind"] == "experiment" and parent.data.tags["notebook"] == "99_test"
    assert parent.data.tags["snapshot"] == data.snapshot.name
    assert parent.data.params["embedding"] == "minilm"
    children = mlflow.search_runs(experiment_names=["exp-test"], output_format="list",
                                  filter_string=f"tags.mlflow.parentRunId = '{parent_id}'")
    assert sorted(c.info.run_name for c in children) == ["baseline popularity", "baseline random", "linear"]
    linear = next(c for c in children if c.info.run_name == "linear")
    assert "test_mrr" in linear.data.metrics
    history = mlflow.MlflowClient().get_metric_history(linear.info.run_id, "curve_val_loss")
    assert len(history) == int(linear.data.params["epochs_run"])


def test_experiments_never_log_to_the_pipeline_experiment():
    assert ex.EXPERIMENT != train.EXPERIMENT


def test_custom_model_with_a_pinned_revision():
    model = ex.custom_model("intfloat/multilingual-e5-base", 768, "query: ", revision="abc123")
    assert (model.key, model.revision, model.prefix, model.dim) == ("intfloat-multilingual-e5-base", "abc123",
                                                                    "query: ", 768)
