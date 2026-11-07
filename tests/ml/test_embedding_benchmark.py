"""ml/embedding_benchmark.py on sample data, with the stand-in encoder and a throwaway MLflow store."""
from __future__ import annotations

import json

import mlflow
import numpy as np
import pytest

from ml import embedding_benchmark as bench
from ml import export, validate
from scripts import make_sample_gameplay_db as sample


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "gameplay.db"
    sample.generate(db, games=40, seed=4)
    folder, _ = export.export(db, tmp_path_factory.mktemp("snapshots"))
    assert all(r.ok for r in validate.validate_snapshot(folder, first_day=sample.FIRST_DAY))
    return folder


def test_logs_one_run_per_model(snapshot, tmp_path, stem_encoders, mlflow_dir):
    results = bench.run(snapshot, ["minilm", "e5"], cache_dir=tmp_path / "emb", experiment="bench-test",
                        encoder_factory=stem_encoders)
    assert [r["model"] for r in results] == ["minilm", "e5"]
    manifest = json.loads((snapshot / export.MANIFEST).read_text())
    runs = mlflow.search_runs(experiment_names=["bench-test"], output_format="list")
    assert sorted(r.info.run_name for r in runs) == ["e5", "minilm"]
    for run in runs:
        assert run.data.tags["snapshot_hash"] == manifest["content_hash"]
        assert run.data.params["revision"] == bench.features.EMBEDDING_MODELS[run.info.run_name].revision
        assert "cpu_priority" in run.data.params
        for metric in ("load_seconds", "encode_seconds", "texts_per_second", "peak_memory_mb", "signal_top1",
                       "signal_sim_gap", "texts"):
            assert metric in run.data.metrics
    # The benchmark fills the cache that training reads.
    assert bench.features.cache_path(bench.features.EMBEDDING_MODELS["e5"], tmp_path / "emb").is_file()


def test_the_planted_topic_signal_shows(snapshot, tmp_path, stem_encoders, mlflow_dir):
    """Sample players prefer cards on the black card's topic, and the stand-in encoder sees topic stems."""
    result = bench.run(snapshot, ["minilm"], cache_dir=tmp_path, encoder_factory=stem_encoders)[0]
    assert result["signal_picks"] > 100
    # Several hand cards usually share the black card's topic and quality decides between them, so
    # "most similar" finds the pick well above chance, not every time.
    assert result["signal_top1"] > 1.3 * result["signal_random_top1"]
    assert result["signal_sim_gap"] > 0


def test_similarity_signal_by_hand():
    ids = np.array([1, 2, 3, 4])
    vectors = np.array([[1, 0], [1, 0], [0, 1], [0.6, 0.8]], dtype=np.float32)  # black=1, hand 2, 3, 4
    tables = {"card_texts": [{"text_id": 1, "black": True, "pick": 1}],
              "rounds": [{"game_id": 1, "round_no": 1, "black": 1}],
              "decisions": [{"game_id": 1, "round_no": 1, "is_model": False, "hand": [3, 2, 4], "picks": [2]},
                            {"game_id": 1, "round_no": 1, "is_model": True, "hand": [3, 2, 4], "picks": [3]},
                            {"game_id": 1, "round_no": 1, "is_model": False, "hand": [3, 2, 4], "picks": None}]}
    signal = bench.similarity_signal(tables, ids, vectors)
    assert signal["signal_picks"] == 1 and signal["signal_top1"] == 1
    assert signal["signal_sim_picked"] == pytest.approx(1)
    assert signal["signal_sim_unpicked"] == pytest.approx(0.3)
    assert bench.similarity_signal({**tables, "decisions": []}, ids, vectors) == {"signal_picks": 0.0}


def test_unvalidated_snapshots_are_refused(snapshot, tmp_path, stem_encoders, mlflow_dir):
    (snapshot / validate.REPORT).rename(snapshot / "report.bak")
    try:
        with pytest.raises(ValueError, match="hasn't been validated"):
            bench.run(snapshot, ["minilm"], cache_dir=tmp_path, encoder_factory=stem_encoders)
    finally:
        (snapshot / "report.bak").rename(snapshot / validate.REPORT)
    report = json.loads((snapshot / validate.REPORT).read_text())
    broken = {**report, "ok": False, "checks": [{"name": "days", "failures": 3}]}
    (snapshot / validate.REPORT).write_text(json.dumps(broken))
    try:
        with pytest.raises(ValueError, match="failed validation: days"):
            bench.run(snapshot, ["minilm"], cache_dir=tmp_path, encoder_factory=stem_encoders)
    finally:
        (snapshot / validate.REPORT).write_text(json.dumps(report))


def test_latest_snapshot(snapshot, tmp_path):
    assert export.latest_snapshot(snapshot.parent) == snapshot
    with pytest.raises(FileNotFoundError):
        export.latest_snapshot(tmp_path)


def test_main_rejects_unknown_models(capsys):
    assert bench.main(["--models", "gpt9"]) == 2
    assert "gpt9" in capsys.readouterr().err


def test_main_reports_a_missing_snapshot(tmp_path, capsys):
    assert bench.main(["--snapshot", str(tmp_path / "nope"), "--models", "minilm"]) == 1
    assert "Benchmark failed" in capsys.readouterr().err
