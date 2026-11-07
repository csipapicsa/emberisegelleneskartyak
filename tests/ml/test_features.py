"""ml/features.py: embeddings with an on-disk cache keyed by model, revision and text."""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
import pytest

from ml import features, lower_priority


def test_the_three_models_are_pinned():
    assert set(features.EMBEDDING_MODELS) == {"minilm", "e5", "granite"}
    for model in features.EMBEDDING_MODELS.values():
        assert len(model.revision) == 40 and model.dim == 384
    assert features.EMBEDDING_MODELS["e5"].prefix == "query: "


def test_embed_returns_sorted_ids_and_normalised_rows(tmp_path, stem_encoders):
    model = features.EMBEDDING_MODELS["minilm"]
    ids, vectors = features.embed({3: "egy kövér macska", 1: "macskaszőr mindenhol", 2: "a főnök kutyája"},
                                  model, tmp_path, stem_encoders)
    assert ids.tolist() == [1, 2, 3]
    assert vectors.shape == (3, 384) and vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1)
    assert vectors[0] @ vectors[2] > vectors[1] @ vectors[2]  # same stem "macsk"


def test_cache_encodes_each_text_once_and_survives_restarts(tmp_path, stem_encoders):
    model = features.EMBEDDING_MODELS["granite"]
    _, first = features.embed({1: "egy", 2: "kettő"}, model, tmp_path, stem_encoders)
    _, again = features.embed({1: "egy", 2: "kettő"}, model, tmp_path, stem_encoders)
    assert len(stem_encoders.made) == 1, "nothing missing, so no model is loaded"
    assert np.array_equal(first, again)
    features.embed({1: "egy", 2: "kettő", 3: "három"}, model, tmp_path, stem_encoders)
    assert stem_encoders.made[-1].calls == [["három"]]
    assert features.cache_path(model, tmp_path).is_file()


def test_cache_is_keyed_by_text_not_by_id(tmp_path, stem_encoders):
    """Text IDs are local to one gameplay.db; the sample cards reuse real IDs."""
    model = features.EMBEDDING_MODELS["minilm"]
    features.embed({1: "valódi kártya"}, model, tmp_path, stem_encoders)
    features.embed({1: "minta kártya"}, model, tmp_path, stem_encoders)
    _, vectors = features.embed({7: "valódi kártya", 8: "minta kártya"}, model, tmp_path, stem_encoders)
    assert len(stem_encoders.made) == 2 and vectors.shape == (2, 384)


def test_prefix_is_part_of_what_is_encoded_and_cached(tmp_path, stem_encoders):
    e5, minilm = features.EMBEDDING_MODELS["e5"], features.EMBEDDING_MODELS["minilm"]
    features.embed({1: "macska"}, e5, tmp_path, stem_encoders)
    assert stem_encoders.made[-1].calls == [["query: macska"]]
    assert features.text_key(e5, "macska") != features.text_key(minilm, "macska")


def test_wrong_vector_shape_is_refused(tmp_path):
    class Broken:
        def __init__(self, model):
            pass

        def encode(self, texts):
            return np.zeros((len(texts), 10), dtype=np.float32)

    with pytest.raises(ValueError, match="384"):
        features.embed({1: "x"}, features.EMBEDDING_MODELS["minilm"], tmp_path, Broken)
    assert not features.cache_path(features.EMBEDDING_MODELS["minilm"], tmp_path).exists()


def test_empty_input(tmp_path, stem_encoders):
    ids, vectors = features.embed({}, features.EMBEDDING_MODELS["minilm"], tmp_path, stem_encoders)
    assert ids.shape == (0,) and vectors.shape == (0, 384) and not stem_encoders.made


def test_lower_priority_puts_the_process_last():
    """In a child process: the test runner itself must keep its normal priority."""
    code = ("import os, ml; print(ml.lower_priority()); "
            "print(os.sched_getscheduler(0) == os.SCHED_IDLE, os.nice(0))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__)))).stdout.split("\n")
    assert out[0] == "SCHED_IDLE, nice 19"
    assert out[1] == "True 19"
    assert callable(lower_priority)


def test_data_dir_follows_eek_ml_data_dir(tmp_path):
    """The training image points snapshots and the embeddings cache at its /data volume."""
    code = ("from ml import export, features, pipeline; "
            "print(export.DEFAULT_OUT, features.DEFAULT_CACHE, pipeline.load_config().snapshots, sep='|')")
    env = {**os.environ, "EEK_ML_DATA_DIR": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env,
                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__)))).stdout.strip()
    assert out.split("|") == [str(tmp_path / "snapshots"), str(tmp_path / "embeddings"), str(tmp_path / "snapshots")]
