"""ml/promote.py: challenger -> champion with a comparison on the newest test picks, refusal of a worse
challenger, rollback. A model trained on sample data in a throwaway MLflow store, the stand-in encoder."""
from __future__ import annotations

import mlflow
import pytest

from ml import evaluate, export, features, promote, train, validate
from scripts import make_sample_gameplay_db as sample

from .conftest import StemEncoder

torch = pytest.importorskip("torch")
MODEL = train.REGISTERED_MODEL
WORLD: dict = {}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A snapshot, a registry with one trained version (challenger), and a deliberately bad version."""
    mp = pytest.MonkeyPatch()
    mp.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri((tmp_path_factory.mktemp("mlflow") / "mlruns").as_uri())
    db = tmp_path_factory.mktemp("db") / "gameplay.db"
    sample.generate(db, games=24, seed=5)
    folder, _ = export.export(db, tmp_path_factory.mktemp("snapshots"))
    assert all(r.ok for r in validate.validate_snapshot(folder, first_day=sample.FIRST_DAY))
    cache = tmp_path_factory.mktemp("emb")
    try:
        cycle = train.run_cycle(folder, embeddings=["minilm"], model_types=["linear"], experiment="promote-test",
                                cache_dir=cache, encoder_factory=StemEncoder, variants=["A"])
        good = cycle["challenger"]["version"]
        WORLD.update(snapshot=folder, cache=cache, good=str(good))
        yield WORLD
    finally:
        mlflow.set_tracking_uri(None)
        mp.undo()


_compare = promote.compare  # the real one: test_promotion... patches promote.compare for main()


def compare(world):
    return _compare(world["snapshot"], cache_dir=world["cache"], encoder_factory=StemEncoder)


def register_copy(version: str, negate: bool = False) -> str:
    """Register another version of the same model (negate: score cards the opposite way, i.e. worse)."""
    client = mlflow.MlflowClient()
    src = client.get_model_version(MODEL, version)
    if not negate:
        new = mlflow.register_model(f"models:/{MODEL}/{version}", MODEL, tags=src.tags)
    else:
        # A scorer trained on the same data, with its sign flipped: it ranks cards the opposite way.
        _, tables = export.read_snapshot(WORLD["snapshot"])
        splits = evaluate.make_splits(tables, "A")
        ids, vectors = features.embed({c["text_id"]: c["text"] for c in tables["card_texts"]},
                                      features.EMBEDDING_MODELS["minilm"], WORLD["cache"], StemEncoder)
        row = {int(t): i for i, t in enumerate(ids)}
        data = {k: train.to_tensors(splits[k], row, vectors) for k in ("train", "val")}
        model, _, _ = train.fit(data["train"], data["val"], 384, train.TrainConfig(lr=0.05, weight_decay=0,
                                                                                   max_epochs=100, patience=10))
        with torch.no_grad():
            for param in model.head.parameters():
                param.neg_()
        with mlflow.start_run(run_name="bad"):
            uri = train._log_model(model, data["train"])
        new = mlflow.register_model(uri, MODEL, tags=src.tags)
    client.set_registered_model_alias(MODEL, promote.CHALLENGER, str(new.version))
    return str(new.version)


def aliases() -> dict[str, str | None]:
    client = mlflow.MlflowClient()
    return {a: (str(v.version) if (v := promote._version(client, a)) else None)
            for a in (promote.CHAMPION, promote.PREVIOUS, promote.CHALLENGER)}


def test_promotion_rollback_and_refusal(world, monkeypatch, capsys):
    # 1. No champion yet: the challenger becomes the first one.
    first = compare(world)
    assert first["verdict"] == "first champion" and first["champion"] is None
    assert first["n_test"] > 0 and 0 < first["challenger"].metrics["mrr"] <= 1
    promote.promote(first)
    assert aliases()[promote.CHAMPION] == world["good"]
    tags = mlflow.MlflowClient().get_model_version(MODEL, world["good"]).tags
    assert tags["promotion_verdict"] == "first champion" and tags["promoted_from"] == "none"

    # 2. Same version again: nothing to do.
    assert compare(world)["verdict"] == "already champion"

    # 3. An identical model under a new version: no significant difference, promoted after "yes".
    twin = register_copy(world["good"])
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(promote, "compare", lambda snapshot=None: compare(world))
    assert promote.main([]) == 0
    out = capsys.readouterr().out
    assert "no significant difference" in out and f"Champion: version {twin}" in out
    assert aliases()[promote.CHAMPION] == twin and aliases()[promote.PREVIOUS] == world["good"]

    # 4. A much worse challenger is refused, and --dry-run never moves anything.
    bad = register_copy(world["good"], negate=True)
    assert promote.main(["--dry-run"]) == 0
    assert promote.main(["--yes"]) == 1
    assert "significantly worse" in capsys.readouterr().out
    assert aliases()[promote.CHAMPION] == twin, "the champion stays"

    # 5. Saying "no" keeps everything as it is.
    register_copy(world["good"])
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert promote.main([]) == 0 and aliases()[promote.CHAMPION] == twin

    # 6. Rollback swaps champion and previous-champion; twice restores.
    assert promote.main(["--rollback"]) == 0
    assert aliases()[promote.CHAMPION] == world["good"] and aliases()[promote.PREVIOUS] == twin
    promote.rollback()
    assert aliases()[promote.CHAMPION] == twin
    assert bad not in (aliases()[promote.CHAMPION], aliases()[promote.PREVIOUS])


def test_rollback_without_a_previous_champion_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
    try:
        assert promote.main(["--rollback"]) == 1
        assert "no 'previous-champion'" in capsys.readouterr().err
    finally:
        mlflow.set_tracking_uri(None)


@pytest.mark.parametrize("verdict, promoted", [
    ("first champion", True), ("significantly better", True),
    ("no significant difference", False), ("significantly worse", False),
])
def test_auto_promotes_only_beyond_the_noise(monkeypatch, capsys, verdict, promoted):
    """--auto (the nightly pipeline) moves the champion only for the first model or a significantly better one."""
    class S:
        version = "8"
        embedding = "e5"
        metrics = {"mrr": 0.4, "top1": 0.2, "top3": 0.45}
    result = {"snapshot": "s", "n_test": 700, "n_games": 20, "challenger": S(), "champion": None,
              "bootstrap": None, "verdict": verdict}
    calls = []
    monkeypatch.setattr(promote, "compare", lambda snapshot=None: result)
    monkeypatch.setattr(promote, "promote", lambda r: calls.append(r) or {"champion": "8", "previous": "7"})
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("--auto must never ask"))

    assert promote.main(["--auto"]) == 0
    assert bool(calls) is promoted
    out = capsys.readouterr().out
    assert ("Champion: version 8" in out) if promoted else (f"Champion kept: {verdict}." in out)
