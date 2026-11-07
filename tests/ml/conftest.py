"""The ML tests need requirements-ml.txt (venv-ml locally, the `ml` job in CI). The game's own virtualenv
doesn't have it, so there they are skipped instead of failing to import."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import pytest

pytest.importorskip("pyarrow")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from ml.features import EmbeddingModel  # noqa: E402


class StemEncoder:
    """A tiny stand-in for the real embedding models: every word adds to the dimension its first five
    letters hash to, so words with the same stem ("macska", "macskaszőr") point the same way. Enough for
    the planted topic signal in the sample data; no download, no torch."""

    calls: list[list[str]]

    def __init__(self, model: EmbeddingModel) -> None:
        self.dim = model.dim
        self.calls = []

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        self.calls.append(list(texts))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                if len(word) >= 3 and word != "query":
                    out[row, int(hashlib.md5(word[:5].encode()).hexdigest(), 16) % self.dim] += 1
            norm = np.linalg.norm(out[row])
            out[row] = out[row] / norm if norm else out[row]
        return out


@pytest.fixture
def stem_encoders():
    """An encoder factory that keeps every encoder it made, so tests can see what was encoded."""
    made: list[StemEncoder] = []

    def factory(model: EmbeddingModel) -> StemEncoder:
        made.append(StemEncoder(model))
        return made[-1]

    factory.made = made
    return factory


@pytest.fixture
def mlflow_dir(tmp_path, monkeypatch):
    """A throwaway local MLflow store instead of the server."""
    import mlflow

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    store = tmp_path / "mlruns"
    mlflow.set_tracking_uri(store.as_uri())
    yield store
    mlflow.set_tracking_uri(None)
