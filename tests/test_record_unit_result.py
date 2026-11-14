"""scripts/record_unit_result.sh: how a systemd run ended, as textfile-collector metrics (the training alerts)."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "record_unit_result.sh"


def record(tmp_path: Path, result: str | None) -> None:
    env = {"PATH": os.environ["PATH"], "TEXTFILE_DIR": str(tmp_path)}
    if result is not None:
        env["SERVICE_RESULT"] = result
    subprocess.run([str(SCRIPT), "eek_ml_train"], env=env, check=True)


def metrics(path: Path) -> dict[str, float]:
    lines = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
    return {name: float(value) for name, value in (line.rsplit(" ", 1) for line in lines)}


def test_success_writes_both_files(tmp_path):
    record(tmp_path, "success")
    run = metrics(tmp_path / "eek_ml_train.prom")
    assert run['eek_ml_train_last_run_success{result="success"}'] == 1
    success = metrics(tmp_path / "eek_ml_train_success.prom")
    assert success["eek_ml_train_last_success_timestamp_seconds"] == run["eek_ml_train_last_run_timestamp_seconds"]


def test_a_failure_keeps_the_last_success(tmp_path):
    record(tmp_path, "success")
    before = (tmp_path / "eek_ml_train_success.prom").read_text()
    record(tmp_path, "oom-kill")
    assert metrics(tmp_path / "eek_ml_train.prom")['eek_ml_train_last_run_success{result="oom-kill"}'] == 0
    assert (tmp_path / "eek_ml_train_success.prom").read_text() == before


def test_without_a_result_it_counts_as_failed(tmp_path):
    record(tmp_path, None)
    assert metrics(tmp_path / "eek_ml_train.prom")['eek_ml_train_last_run_success{result="unknown"}'] == 0
    assert not (tmp_path / "eek_ml_train_success.prom").exists()


def test_files_are_readable_by_node_exporter_and_no_temp_files_remain(tmp_path):
    record(tmp_path, "success")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["eek_ml_train.prom", "eek_ml_train_success.prom"]
    for path in tmp_path.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
