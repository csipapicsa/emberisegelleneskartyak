"""ml/pipeline.py: export -> validate -> train only when there's enough new data. On sample data."""
from __future__ import annotations

import shutil
import sqlite3

import mlflow
import pytest

from ml import pipeline, train
from scripts import make_sample_gameplay_db as sample

from .conftest import StemEncoder

ORIGINAL_RUN = pipeline.run  # before test_main replaces it


@pytest.fixture
def config(tmp_path):
    db = tmp_path / "gameplay.db"
    sample.generate(db, games=18, seed=6)
    return pipeline.Config(db=db, snapshots=tmp_path / "snapshots", first_day=sample.FIRST_DAY,
                           embeddings=["minilm"], models=["linear"], experiment="pipeline-test", min_new_picks=20)


def run(config, **kwargs):
    return pipeline.run(config, encoder_factory=StemEncoder, **kwargs)


def add_games(config, tmp_path, games: int) -> None:
    """Append `games` new sample games (higher IDs, later days) to the gameplay database."""
    extra = tmp_path / f"extra-{games}.db"
    sample.generate(extra, games=games, seed=99)
    conn = sqlite3.connect(config.db)
    offset = conn.execute("SELECT MAX(game_id) FROM game").fetchone()[0]
    last_day = conn.execute("SELECT MAX(day) FROM game").fetchone()[0]
    conn.execute("ATTACH ? AS extra", (str(extra),))
    conn.execute("INSERT INTO game SELECT game_id + ?, day - ? + ? + 1, vote_mode, self_vote, rounds, timer, editions, "
                 "app_version FROM extra.game", (offset, sample.FIRST_DAY, last_day))
    for table in ("game_player", "round", "decision", "hand_change", "vote"):
        cols = [r[1] for r in conn.execute(f"PRAGMA extra.table_info({table})")]
        select = ", ".join("game_id + ?" if c == "game_id" else c for c in cols)
        conn.execute(f"INSERT INTO {table} SELECT {select} FROM extra.{table}", (offset,))
    conn.commit()
    conn.close()


def test_trains_once_then_waits_for_new_data(config, tmp_path, mlflow_dir):
    first = run(config)
    assert first["status"] == "trained" and first["reason"] == "first cycle"
    assert [r["name"] for r in first["runs"]] == ["baseline random", "baseline popularity", "linear minilm"]

    again = run(config)
    assert again["status"] == "skipped" and "already trained" in again["reason"]

    add_games(config, tmp_path, 1)  # new picks, but fewer than the threshold asks for
    config.min_new_picks = 100_000
    few = run(config)
    assert few["status"] == "skipped" and "fewer than 100000" in few["reason"]

    config.min_new_picks = 20
    add_games(config, tmp_path, 6)
    more = run(config)
    assert more["status"] == "trained" and "new picks" in more["reason"]
    assert more["picks"] - first["picks"] >= 20

    forced = run(config, force=True)
    assert forced["status"] == "trained" and forced["reason"] == "forced"
    cycles = mlflow.search_runs(experiment_names=["pipeline-test"], filter_string="tags.kind = 'cycle'",
                                output_format="list")
    assert len(cycles) == 3


def test_last_cycle_reads_mlflow(config, mlflow_dir):
    assert pipeline.last_cycle("pipeline-test") is None
    result = run(config)
    last = pipeline.last_cycle("pipeline-test")
    assert last["run_id"] == result["parent_run_id"]
    assert last["picks"] == result["picks"] > 0


def test_each_cycle_stores_its_snapshot(config, mlflow_dir):
    result = run(config)
    names = {a.path for a in mlflow.MlflowClient().list_artifacts(result["parent_run_id"], "snapshot")}
    assert {"snapshot/manifest.json", "snapshot/decisions.parquet", "snapshot/validation.json"} <= names


def test_invalid_data_stops_before_training(config, mlflow_dir):
    config.first_day = sample.FIRST_DAY + 10_000  # every sample game now looks impossibly early
    result = run(config)
    assert result["status"] == "invalid" and "days" in result["reason"]
    assert pipeline.last_cycle("pipeline-test") is None


def test_load_config(tmp_path):
    shipped = pipeline.load_config()
    assert shipped.embeddings == ["minilm", "e5", "granite"] and shipped.models == ["linear", "mlp"]
    assert shipped.min_new_picks == 20 and shipped.first_day == 20763 and shipped.register
    assert shipped.db == pipeline.DB_PATH

    custom = tmp_path / "c.toml"
    custom.write_text('[export]\ndb = "x/g.db"\n[train]\nmodels = ["mlp"]\nregister = false\n'
                      '[trigger]\nmin_new_picks = 5\n')
    c = pipeline.load_config(custom)
    assert c.db == pipeline.ROOT / "x/g.db" and c.models == ["mlp"] and not c.register and c.min_new_picks == 5

    custom.write_text('[train]\nembeddings = ["bert"]\n')
    with pytest.raises(ValueError, match="bert"):
        pipeline.load_config(custom)


def test_main(config, tmp_path, mlflow_dir, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "load_config", lambda path: config)
    monkeypatch.setattr(pipeline, "run", lambda c, force=False: ORIGINAL_RUN(c, force=force, encoder_factory=StemEncoder))
    assert pipeline.main([]) == 0
    assert "Trained (first cycle)" in capsys.readouterr().out
    assert pipeline.main([]) == 0
    assert "Nothing to train" in capsys.readouterr().out
    config.first_day = sample.FIRST_DAY + 10_000
    assert pipeline.main(["--force"]) == 1
    assert "Stopped: failed checks: days" in capsys.readouterr().err
    config.db = tmp_path / "missing.db"
    assert pipeline.main([]) == 1
    assert "Pipeline failed" in capsys.readouterr().err


def test_copy_of_config_file_is_valid_toml(tmp_path):
    copy = tmp_path / "pipeline.toml"
    shutil.copy(pipeline.DEFAULT_CONFIG, copy)
    assert pipeline.load_config(copy).experiment == train.EXPERIMENT
