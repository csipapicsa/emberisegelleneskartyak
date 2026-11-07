"""Card text embeddings with the three small multilingual models, cached on disk.

Each card text becomes a 384-dimensional, length-normalised vector, so cosine similarity is a dot product.
Card texts never change once logged (a reworded card gets a new text_id), so an embedding is computed once
per (model, model revision, text) and cached. The cache is keyed by the text itself, not by text_id: text
IDs are local to one gameplay.db, and the made-up sample cards share IDs with real ones.

    from ml.features import EMBEDDING_MODELS, embed
    ids, vectors = embed({1: "Mit csinál a macska éjjel? ____"}, EMBEDDING_MODELS["e5"])

Models are pinned to a Hugging Face commit, so a run can be rebuilt with the exact same weights.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "ml-data" / "embeddings"


@dataclass(frozen=True)
class EmbeddingModel:
    key: str            # short name used in run names and paths
    hf_id: str          # Hugging Face model ID
    revision: str       # Hugging Face commit, so the weights can't change under a run
    prefix: str = ""    # prepended to every text (e5 expects "query: " for symmetric similarity)
    dim: int = 384


EMBEDDING_MODELS: dict[str, EmbeddingModel] = {m.key: m for m in (
    EmbeddingModel("minilm", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                   "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"),
    EmbeddingModel("e5", "intfloat/multilingual-e5-small", "614241f622f53c4eeff9890bdc4f31cfecc418b3",
                   prefix="query: "),
    EmbeddingModel("granite", "ibm-granite/granite-embedding-97m-multilingual-r2",
                   "835ad14087e140460703cf0fae09f97d469d65c2"),
)}


class Encoder(Protocol):
    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Return one length-normalised float32 row per text."""


class SentenceTransformerEncoder:
    """The real model, loaded on first use (sentence-transformers and torch are imported only here)."""

    def __init__(self, model: EmbeddingModel, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model.hf_id, revision=model.revision, device="cpu")
        self._batch_size = batch_size

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(list(texts), batch_size=self._batch_size, normalize_embeddings=True,
                                     show_progress_bar=False, convert_to_numpy=True)
        return np.asarray(vectors, dtype=np.float32)


EncoderFactory = Callable[[EmbeddingModel], Encoder]


def text_key(model: EmbeddingModel, text: str) -> str:
    """Cache key: what the model actually reads, i.e. the text with the model's prefix."""
    return hashlib.sha256((model.prefix + text).encode()).hexdigest()


def cache_path(model: EmbeddingModel, cache_dir: Path = DEFAULT_CACHE) -> Path:
    return Path(cache_dir) / model.key / f"{model.revision[:12]}.parquet"


def _load_cache(path: Path) -> dict[str, np.ndarray]:
    if not path.is_file():
        return {}
    table = pq.read_table(path)
    keys = table.column("key").to_pylist()
    vectors = np.asarray(table.column("vector").to_pylist(), dtype=np.float32)
    return dict(zip(keys, vectors, strict=True))


def _save_cache(path: Path, cache: dict[str, np.ndarray], dim: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted(cache)
    table = pa.table({"key": pa.array(keys, pa.string()),
                      "vector": pa.array([cache[k].tolist() for k in keys], pa.list_(pa.float32(), dim))})
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def embed(texts: dict[int, str], model: EmbeddingModel, cache_dir: Path = DEFAULT_CACHE,
          encoder_factory: EncoderFactory = SentenceTransformerEncoder) -> tuple[np.ndarray, np.ndarray]:
    """Embeddings for {text_id: text}: (text IDs sorted, matrix with one row per ID). Only texts missing
    from the cache are encoded, and the model is loaded only if there are any."""
    path = cache_path(model, cache_dir)
    cache = _load_cache(path)
    ids = np.array(sorted(texts), dtype=np.int64)
    keys = [text_key(model, texts[i]) for i in ids]
    missing = sorted({k: model.prefix + texts[i] for i, k in zip(ids, keys, strict=True) if k not in cache}.items())
    if missing:
        vectors = encoder_factory(model).encode([text for _, text in missing])
        if vectors.shape != (len(missing), model.dim):
            raise ValueError(f"{model.key}: expected {len(missing)} x {model.dim} vectors, got {vectors.shape}")
        cache.update((key, vector) for (key, _), vector in zip(missing, vectors, strict=True))
        _save_cache(path, cache, model.dim)
    matrix = np.stack([cache[k] for k in keys]) if keys else np.zeros((0, model.dim), dtype=np.float32)
    return ids, matrix
