"""scripts/check_ml_lock.py: the cheap stand-in for recompiling requirements-ml.txt in CI."""
from __future__ import annotations

from scripts import check_ml_lock as check

ML_IN = """\
--extra-index-url https://download.pytorch.org/whl/cpu
-c requirements-dev.txt
torch==2.14.1+cpu              # comment
Sentence_Transformers==6.2.0
"""
ML_TXT = """\
numpy==2.4.6 \\
    --hash=sha256:aa
    # via torch
sentence-transformers==6.2.0 \\
    --hash=sha256:bb
torch==2.14.1+cpu \\
    --hash=sha256:cc \\
    --hash=sha256:dd
"""
DEV_TXT = """\
numpy==2.4.6 \\
    --hash=sha256:aa
pytest==9.0.3 \\
    --hash=sha256:ee
"""


def test_pins_normalises_names_and_ignores_the_rest():
    assert check.pins(ML_IN) == {"torch": "2.14.1+cpu", "sentence-transformers": "6.2.0"}
    assert check.pins(ML_TXT)["numpy"] == "2.4.6"


def test_consistent_files_pass():
    assert check.problems(ML_IN, ML_TXT, DEV_TXT) == []


def test_a_direct_pin_that_drifted():
    found = check.problems(ML_IN.replace("6.2.0", "6.3.0"), ML_TXT, DEV_TXT)
    assert found == ["sentence-transformers: requirements-ml.in pins 6.3.0, requirements-ml.txt has 6.2.0"]


def test_a_shared_package_with_two_versions():
    found = check.problems(ML_IN, ML_TXT, DEV_TXT.replace("2.4.6", "2.4.5"))
    assert found == ["numpy: 2.4.6 in requirements-ml.txt, 2.4.5 in requirements-dev.txt"]


def test_a_pin_without_hash():
    found = check.problems(ML_IN, ML_TXT.replace("    --hash=sha256:bb\n", ""), DEV_TXT)
    assert found == ["sentence-transformers: no hash in requirements-ml.txt"]


def test_the_real_lock_files_are_consistent(capsys):
    assert check.main() == 0
    assert "matches" in capsys.readouterr().out


def test_main_reports_problems(tmp_path, capsys):
    (tmp_path / "requirements-ml.in").write_text(ML_IN.replace("6.2.0", "9.9.9"))
    (tmp_path / "requirements-ml.txt").write_text(ML_TXT)
    (tmp_path / "requirements-dev.txt").write_text(DEV_TXT)
    assert check.main(tmp_path) == 1
    err = capsys.readouterr().err
    assert "9.9.9" in err and "Regenerate" in err
