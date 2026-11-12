"""ml/serve.py and ml/serve_bench.py: a model trained on sample data, registered in a throwaway MLflow store,
served with the stand-in encoder."""
from __future__ import annotations

import json

import mlflow
import numpy as np
import pytest

from ml import export, serve, serve_bench, train, validate
from scripts import make_sample_gameplay_db as sample

from .conftest import StemEncoder

pytest.importorskip("torch")
from fastapi.testclient import TestClient  # noqa: E402

URI = f"models:/{train.REGISTERED_MODEL}@challenger"


class CountingEncoder(StemEncoder):
    texts: list[str] = []

    def encode(self, texts):
        CountingEncoder.texts.extend(texts)
        return super().encode(texts)


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    """One training cycle on sample data: pick-model version 1, alias challenger, raw scores."""
    mp = pytest.MonkeyPatch()
    mp.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri((tmp_path_factory.mktemp("mlflow") / "mlruns").as_uri())
    db = tmp_path_factory.mktemp("db") / "gameplay.db"
    sample.generate(db, games=24, seed=5)
    folder, _ = export.export(db, tmp_path_factory.mktemp("snapshots"))
    assert all(r.ok for r in validate.validate_snapshot(folder, first_day=sample.FIRST_DAY))
    try:
        cycle = train.run_cycle(folder, embeddings=["minilm"], model_types=["linear"], experiment="serve-test",
                                cache_dir=tmp_path_factory.mktemp("emb"), encoder_factory=StemEncoder)
        yield cycle
    finally:
        mlflow.set_tracking_uri(None)
        mp.undo()


@pytest.fixture
def scorer(registry):
    s = serve.Scorer(URI, encoder_factory=CountingEncoder)
    assert s.update()
    return s


def test_uri_must_name_an_alias_or_a_version():
    assert serve.Scorer("models:/pick-model/3").fixed_version == "3"
    assert serve.Scorer("models:/pick-model@champion").alias == "champion"
    with pytest.raises(ValueError, match="models:/"):
        serve.Scorer("runs:/abc/model")


def test_scores_any_number_of_cards_and_padding_changes_nothing(scorer):
    black = "Mitől ragadok? ____."
    words = ["macska", "kecske", "pálinka", "parlament", "lángos", "szomszéd", "zokni", "kolbász", "metró",
             "fogorvos", "nyugdíj", "kísértet", "lakodalom"]
    cards = [f"A {w} és a {words[(i + 5) % 13]}." for i, w in enumerate(words)]
    whole = scorer.score(black, cards)
    assert len(whole["scores"]) == 13 and whole["model"] == "pick-model" and whole["version"] == "1"
    assert sum(whole["probs"]) == pytest.approx(1)
    alone = [scorer.score(black, [c])["scores"][0] for c in cards[:3]]
    assert np.allclose(alone, whole["scores"][:3], atol=1e-5), "a card's score doesn't depend on the others"
    assert np.argmax(whole["probs"]) == np.argmax(whole["scores"])
    assert len(set(np.round(whole["scores"], 6))) > 1, "different cards, different scores"


def test_new_cards_are_embedded_once_then_cached(scorer):
    CountingEncoder.texts = []
    scorer.score("Új fekete lap ____.", ["Vadonatúj kártya.", "Másik új kártya."])
    assert len(CountingEncoder.texts) == 3
    scorer.score("Új fekete lap ____.", ["Vadonatúj kártya.", "Harmadik új kártya."])
    assert CountingEncoder.texts[3:] == ["Harmadik új kártya."], "only the unseen card is encoded"


def test_cache_stays_within_its_size(registry):
    s = serve.Scorer(URI, encoder_factory=StemEncoder, cache_size=5)
    s.update()
    s.score("Fekete ____.", [f"kártya {i}" for i in range(12)])
    assert len(s._cache) == 5


def test_refresh_cutoff_comes_from_the_model_tags(scorer):
    version = mlflow.MlflowClient().get_model_version_by_alias(train.REGISTERED_MODEL, "challenger")
    cutoffs = json.loads(version.tags["best_score_percentiles"])
    assert scorer.score("Fekete ____.", ["kártya"])["refresh_below"] == pytest.approx(cutoffs["10"])
    s20 = serve.Scorer(URI, refresh_percentile=20, encoder_factory=StemEncoder)
    s20.update()
    assert s20.score("Fekete ____.", ["kártya"])["refresh_below"] == pytest.approx(cutoffs["20"])
    s7 = serve.Scorer(URI, refresh_percentile=7, encoder_factory=StemEncoder)
    s7.update()
    assert s7.score("Fekete ____.", ["kártya"])["refresh_below"] is None, "no such cut-off: never refresh"


def test_follows_the_alias_and_keeps_the_loaded_version_until_it_moves(registry, scorer):
    assert not scorer.update(), "same version: nothing to load"
    client = mlflow.MlflowClient()
    run = client.get_model_version(train.REGISTERED_MODEL, "1")
    v2 = mlflow.register_model(f"models:/{train.REGISTERED_MODEL}/1", train.REGISTERED_MODEL, tags=run.tags)
    client.set_registered_model_alias(train.REGISTERED_MODEL, "challenger", v2.version)
    try:
        assert scorer.update() and scorer.bundle.version == str(v2.version)
        assert scorer.score("Fekete ____.", ["kártya"])["version"] == str(v2.version)
    finally:
        client.set_registered_model_alias(train.REGISTERED_MODEL, "challenger", "1")


def test_refuses_a_version_that_returns_probabilities(registry):
    tags = {k: v for k, v in mlflow.MlflowClient().get_model_version(train.REGISTERED_MODEL, "1").tags.items()
            if k != "output"}
    old = mlflow.register_model(f"models:/{train.REGISTERED_MODEL}/1", train.REGISTERED_MODEL, tags=tags)
    s = serve.Scorer(f"models:/{train.REGISTERED_MODEL}/{old.version}", encoder_factory=StemEncoder)
    assert not s.update() and s.bundle is None
    assert "probabilities" in s.error
    ok, body = s.health()
    assert not ok and body["status"] == "error"


def test_http_endpoints(registry, scorer):
    client = TestClient(serve.create_app(scorer, poll_seconds=None))
    health = client.get("/healthz")
    assert health.status_code == 200 and health.json()["version"] == "1"
    answer = client.post("/score", json={"black": "Fekete ____.", "cards": ["egy", "kettő"]})
    assert answer.status_code == 200 and len(answer.json()["scores"]) == 2
    assert client.post("/score", json={"black": "Fekete ____.", "cards": []}).status_code == 422
    assert client.post("/score", json={"black": "x", "cards": ["c"] * (serve.MAX_CARDS + 1)}).status_code == 422
    assert client.post("/score", json={"black": "x", "cards": [""]}).status_code == 422


def test_http_before_the_model_is_loaded(registry):
    client = TestClient(serve.create_app(serve.Scorer(URI, encoder_factory=StemEncoder), poll_seconds=None))
    assert client.get("/healthz").status_code == 503
    assert client.get("/healthz").json()["status"] == "loading"
    assert client.post("/score", json={"black": "x", "cards": ["c"]}).status_code == 503


def test_background_loading_makes_the_service_healthy(registry):
    with TestClient(serve.create_app(serve.Scorer(URI, encoder_factory=StemEncoder), poll_seconds=60)) as client:
        for _ in range(200):
            if client.get("/healthz").status_code == 200:
                break
            import time
            time.sleep(0.05)
        assert client.get("/healthz").json()["status"] == "ok"


def test_bench_passes_against_the_service(registry, scorer):
    client = TestClient(serve.create_app(scorer, poll_seconds=None))
    result = serve_bench.run(lambda body: client.post("/score", json=body).json(), games=2, rounds=2, players=4)
    assert len(result.turns) == 4 and not result.errors
    assert len(result.calls) == 4 * (1 + serve_bench.MAX_REFRESHES + 1)
    ok, text = serve_bench.summary(result, budget=15)
    assert ok and "PASS" in text


def test_bench_fails_on_slow_turns_and_errors():
    slow = serve_bench.Result(turns=[1.0, 20.0], calls=[0.1])
    ok, text = serve_bench.summary(slow, budget=15)
    assert not ok and "1 turns over budget" in text

    def broken(body):
        raise ConnectionError("refused")
    result = serve_bench.run(broken, games=2, rounds=1)
    ok, text = serve_bench.summary(result, budget=15)
    assert not ok and len(result.errors) == 2 and "refused" in text


def test_bench_cards_are_all_new():
    import random
    rng = random.Random(0)
    cards = [serve_bench.new_card(rng, f"t{i}") for i in range(50)]
    assert len(set(cards)) == 50
    assert "____" in serve_bench.new_card(rng, "b", black=True)
