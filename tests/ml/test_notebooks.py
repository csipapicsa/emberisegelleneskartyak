"""notebooks/*.ipynb stay clean and runnable, and ml/notebooks.py (the command-line runner) finds them."""
from __future__ import annotations

import ast
import json

import pytest

from ml import notebooks
from ml.notebooks import NOTEBOOK_DIR

NOTEBOOKS = notebooks.available()
# The training image (Dockerfile.ml) runs tests/ml but has no notebooks/: it doesn't need them.
needs_notebooks = pytest.mark.skipif(not NOTEBOOK_DIR.is_dir(), reason="notebooks/ isn't in the training image")


@needs_notebooks
def test_there_are_notebooks():
    assert len(NOTEBOOKS) >= 4


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_is_committed_without_outputs(path):
    nb = json.loads(path.read_text())
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [] and cell["execution_count"] is None, \
                f"{path.name}: clear the outputs before committing (executed copies go to ml-data/notebook-runs/)"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_has_a_parameters_cell_and_valid_code(path):
    nb = json.loads(path.read_text())
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    tagged = [c for c in code if "parameters" in c.get("metadata", {}).get("tags", [])]
    assert len(tagged) == 1, "papermill overrides the one cell tagged 'parameters'"
    params = ast.parse("".join(tagged[0]["source"]))
    names = {t.id for node in params.body if isinstance(node, ast.Assign) for t in node.targets}
    assert {"snapshot", "log_mlflow"} <= names
    source = "\n".join("".join(c["source"]) for c in code)
    ast.parse(source)
    assert f'ex.log("{path.stem}"' in source, "logs to MLflow under its own name"


def test_resolve_by_name_prefix_or_all(tmp_path):
    for name in ("01_bilinear", "02_popularity", "10_other"):
        (tmp_path / f"{name}.ipynb").write_text("{}")
    (tmp_path / "scratch.ipynb").write_text("{}")
    assert [p.stem for p in notebooks.resolve(["all"], tmp_path)] == ["01_bilinear", "02_popularity", "10_other"]
    assert [p.stem for p in notebooks.resolve(["02", "01_bilinear"], tmp_path)] == ["02_popularity", "01_bilinear"]
    with pytest.raises(ValueError, match="matches none"):
        notebooks.resolve(["03"], tmp_path)
    (tmp_path / "01_other.ipynb").write_text("{}")
    with pytest.raises(ValueError, match="01_bilinear, 01_other"):
        notebooks.resolve(["01"], tmp_path)


def test_parse_params():
    assert notebooks.parse_params(["rank=16", "embedding=e5", "ranks=[4, 16]", "log_mlflow=False",
                                   "white_prefix=passage: ", "snapshot=None"]) == {
        "rank": 16, "embedding": "e5", "ranks": [4, 16], "log_mlflow": False, "white_prefix": "passage: ",
        "snapshot": None}
    with pytest.raises(ValueError, match="name=value"):
        notebooks.parse_params(["rank"])


@needs_notebooks
def test_main_lists_and_rejects_unknown_names(capsys):
    assert notebooks.main(["list"]) == 0
    assert capsys.readouterr().out.split() == [p.stem for p in NOTEBOOKS]
    assert notebooks.main(["run", "99_nope"]) == 2
    assert "99_nope" in capsys.readouterr().err


@needs_notebooks
def test_notebook_dir_is_the_repository_folder():
    assert NOTEBOOK_DIR.name == "notebooks" and (NOTEBOOK_DIR.parent / "ml").is_dir()
