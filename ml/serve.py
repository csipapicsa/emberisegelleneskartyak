"""Model service for the computer player: scores white cards against a black card, over HTTP.

    python -m ml.serve                 # 0.0.0.0:8020, the models in EEK_MODEL_URIS (default: pick-model@challenger)
    EEK_MODEL_URIS="challenger=models:/pick-model@challenger,champion=models:/pick-model@champion"

    POST /score  {"black": "Mitől ragadok? ____.", "cards": ["A méz.", ...], "model": "champion"}
             ->  {"model": "pick-model", "version": "6", "scores": [...], "probs": [...], "refresh_below": 1.23}
    GET /healthz -> 200 with the loaded models, 503 while none is loaded

One service serves several named models: staging's computer player asks for "challenger", production's for
"champion" (without "model", the first one answers). They share the embedding model and its cache, so a
second model costs only its own few megabytes.

The game (server/bot.py) sends card texts, not IDs, so a card the model has never seen works the same as any
other: its embedding is computed on the spot (~20 ms a card on one CPU thread) and kept in a cache. A score
depends only on the black card and that one white card, so any number of cards can be scored: they're split
into hands of ten and the last hand is padded.

"refresh_below" is the new-hand cut-off: the best card's score in the EEK_REFRESH_PERCENTILE-th percentile
(default 10) of training hands. A hand whose best card scores lower is worse than 90% of the hands the model
learned from. It comes from the model version's tags (ml/train.py), so it moves with the model.

The model follows an MLflow alias: every EEK_MODEL_POLL_SECONDS (default 300) the service checks which
version the alias points to and loads a new one in the background; requests keep using the old one until the
new one is ready. MLflow address and login: MLFLOW_TRACKING_URI, MLFLOW_TRACKING_USERNAME/PASSWORD.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import numpy as np
from pydantic import BaseModel, Field

from ml import features
from ml.evaluate import HAND_SIZE

logger = logging.getLogger("ml.serve")

DEFAULT_MODEL_URI = "models:/pick-model@challenger"
DEFAULT_PORT = 8020
MAX_CARDS = 40          # a hand is 10; a vote sees one submission per player (up to 3 cards each)
MAX_TEXT = 1000
_URI = re.compile(r"^models:/(?P<name>[^/@]+)(?:@(?P<alias>[^/@]+)|/(?P<version>\d+))$")


class ScoreRequest(BaseModel):
    black: str = Field(min_length=1, max_length=MAX_TEXT)
    cards: list[str] = Field(min_length=1, max_length=MAX_CARDS)
    model: str | None = Field(default=None, max_length=64)  # a served model's name; None: the default


@dataclass
class Bundle:
    """One loaded model version with its encoder."""
    name: str
    version: str
    model: object                       # mlflow.pyfunc model: (n, 11, dim) -> (n, 10) scores
    embedding: features.EmbeddingModel
    encoder: features.Encoder
    percentiles: dict[str, float]
    loaded_at: float = field(default_factory=time.time)


class Embeddings:
    """Encoders and an LRU cache of card embeddings, shared by every model the service serves: models that
    use the same embedding model load it once and reuse each other's vectors."""

    def __init__(self, encoder_factory: features.EncoderFactory = features.SentenceTransformerEncoder,
                 cache_size: int = 20000) -> None:
        self._factory = encoder_factory
        self._encoders: dict[features.EmbeddingModel, features.Encoder] = {}
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()

    def encoder(self, embedding: features.EmbeddingModel) -> features.Encoder:
        with self._lock:
            if embedding not in self._encoders:
                self._encoders[embedding] = self._factory(embedding)
            return self._encoders[embedding]

    def embed(self, embedding: features.EmbeddingModel, texts: list[str]) -> np.ndarray:
        keys = [f"{embedding.key}:{embedding.revision}:{features.text_key(embedding, t)}" for t in texts]
        with self._lock:
            found = {k: self._cache[k] for k in keys if k in self._cache}
            for k in found:
                self._cache.move_to_end(k)  # least recently used goes first
        missing = {k: t for k, t in zip(keys, texts, strict=True) if k not in found}
        if missing:
            vectors = self.encoder(embedding).encode([embedding.prefix + t for t in missing.values()])
            found.update(zip(missing, vectors, strict=True))
            with self._lock:
                for k in missing:
                    self._cache[k] = found[k]
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        return np.stack([found[k] for k in keys])

    def __len__(self) -> int:
        return len(self._cache)


class Scorer:
    """Loads the model behind a models:/ URI, follows its alias and scores cards."""

    def __init__(self, uri: str = DEFAULT_MODEL_URI, refresh_percentile: int = 10,
                 encoder_factory: features.EncoderFactory = features.SentenceTransformerEncoder,
                 cache_size: int = 20000, embeddings: Embeddings | None = None) -> None:
        match = _URI.match(uri)
        if not match:
            raise ValueError(f"model URI {uri!r}: expected models:/<name>@<alias> or models:/<name>/<version>")
        self.uri, self.name = uri, match["name"]
        self.alias, self.fixed_version = match["alias"], match["version"]
        self.refresh_percentile = str(refresh_percentile)
        # "is not None": an Embeddings with an empty cache has len() 0, so "or" would replace a shared one
        self.embeddings = embeddings if embeddings is not None else Embeddings(encoder_factory, cache_size)
        self.bundle: Bundle | None = None
        self.error: str | None = None

    def current_version(self) -> str:
        if self.fixed_version:
            return self.fixed_version
        import mlflow
        return str(mlflow.MlflowClient().get_model_version_by_alias(self.name, self.alias).version)

    def update(self) -> bool:
        """Load the version the URI points to now, if it isn't the loaded one. True if a new one was loaded.
        On failure the old version stays and the error is kept for /healthz."""
        try:
            version = self.current_version()
            if self.bundle is not None and self.bundle.version == version:
                return False
            self.bundle = self._load(version)
            self.error = None
            logger.info("loaded %s version %s (%s)", self.name, version, self.bundle.embedding.key)
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            logger.exception("could not load %s", self.uri)
            return False

    def _load(self, version: str) -> Bundle:
        import mlflow
        info = mlflow.MlflowClient().get_model_version(self.name, version)
        if info.tags.get("output") != "scores":
            raise ValueError(f"{self.name} version {version} returns probabilities, not scores; the computer "
                             "player needs a version trained after ml/train.py started logging raw scores")
        embedding = features.EMBEDDING_MODELS[info.tags["embedding"]]
        percentiles = {k: float(v) for k, v in json.loads(info.tags.get("best_score_percentiles", "{}")).items()}
        encoder = self.embeddings.encoder(embedding)
        model = mlflow.pyfunc.load_model(f"models:/{self.name}/{version}")
        return Bundle(self.name, version, model, embedding, encoder, percentiles)

    def score(self, black: str, cards: list[str]) -> dict:
        bundle = self.bundle  # one consistent version for the whole request, even if the poller swaps it
        if bundle is None:
            raise RuntimeError(self.error or "no model loaded yet")
        vectors = self.embeddings.embed(bundle.embedding, [black, *cards])
        black_vec, card_vecs = vectors[0], vectors[1:]
        scores = []
        for start in range(0, len(cards), HAND_SIZE):
            chunk = card_vecs[start:start + HAND_SIZE]
            padded = np.concatenate([chunk, np.repeat(chunk[-1:], HAND_SIZE - len(chunk), axis=0)])
            x = np.concatenate([black_vec[None], padded])[None].astype(np.float32)
            scores.extend(np.asarray(bundle.model.predict(x), dtype=np.float64)[0][:len(chunk)].tolist())
        exp = np.exp(np.asarray(scores) - max(scores))
        return {"model": bundle.name, "version": bundle.version, "scores": scores,
                "probs": (exp / exp.sum()).tolist(), "refresh_below": bundle.percentiles.get(self.refresh_percentile)}

    def health(self) -> tuple[bool, dict]:
        bundle = self.bundle
        if bundle is None:
            return False, {"status": "error" if self.error else "loading", "uri": self.uri, "error": self.error}
        return True, {"status": "ok", "uri": self.uri, "model": bundle.name, "version": bundle.version,
                      "embedding": bundle.embedding.key, "refresh_percentile": self.refresh_percentile,
                      "refresh_below": bundle.percentiles.get(self.refresh_percentile),
                      "loaded_at": bundle.loaded_at, "last_error": self.error}


def parse_models(spec: str) -> dict[str, str]:
    """"challenger=models:/m@challenger,champion=models:/m@champion" -> {name: uri}. A bare URI is one model
    named after its alias (or "default")."""
    models = {}
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        name, sep, uri = part.partition("=")
        if not sep:
            name, uri = (_URI.match(part)["alias"] if _URI.match(part) else None) or "default", part
        if not name.isidentifier() or not _URI.match(uri.strip()):
            raise ValueError(f"model spec {part!r}: expected name=models:/<name>@<alias>")
        models[name] = uri.strip()
    if not models:
        raise ValueError("no model configured")
    return models


def _follow(scorers: dict[str, Scorer], poll_seconds: float, stop: threading.Event) -> None:
    """Load the models, then keep checking their aliases. Until all have loaded, retry every 30 s."""
    while not stop.is_set():
        for scorer in scorers.values():
            scorer.update()
        loaded = all(s.bundle is not None for s in scorers.values())
        stop.wait(poll_seconds if loaded else min(30.0, poll_seconds))


def create_app(scorers: Scorer | dict[str, Scorer], poll_seconds: float | None = 300):
    """The HTTP app for one or several named models (the first is the default). poll_seconds=None: no
    background loading (the caller loads the scorers, as tests do)."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse

    if isinstance(scorers, Scorer):
        scorers = {scorers.alias or "default": scorers}
    default = next(iter(scorers))

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        if poll_seconds is not None:
            threading.Thread(target=_follow, args=(scorers, poll_seconds, stop), name="model-follow",
                             daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="pick-model service", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.post("/score")
    def score(req: ScoreRequest) -> dict:  # a plain def: FastAPI runs it in a worker thread, off the event loop
        if any(not c or len(c) > MAX_TEXT for c in req.cards):
            raise HTTPException(422, f"every card needs 1-{MAX_TEXT} characters")
        scorer = scorers.get(req.model or default)
        if scorer is None:
            raise HTTPException(404, f"no model {req.model!r}; served: {', '.join(scorers)}")
        try:
            return scorer.score(req.black, req.cards)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/healthz")
    def healthz():
        """200 while at least one model is loaded ("degraded" if not all are). The default model's details
        are at the top level, every model's under "models"."""
        health = {name: s.health() for name, s in scorers.items()}
        ok_default, body = health[default]
        body = dict(body)
        loaded = sum(ok for ok, _ in health.values())
        if loaded and loaded < len(health):
            body["status"] = "degraded"
        body["models"] = {name: h for name, (_, h) in health.items()}
        return JSONResponse(body, status_code=200 if loaded else 503)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import torch
    import uvicorn
    torch.set_num_threads(int(os.environ.get("EEK_TORCH_THREADS", "1")))
    shared = Embeddings()
    spec = os.environ.get("EEK_MODEL_URIS") or os.environ.get("EEK_MODEL_URI") or DEFAULT_MODEL_URI
    percentile = int(os.environ.get("EEK_REFRESH_PERCENTILE", "10"))
    scorers = {name: Scorer(uri, refresh_percentile=percentile, embeddings=shared)
               for name, uri in parse_models(spec).items()}
    app = create_app(scorers, poll_seconds=float(os.environ.get("EEK_MODEL_POLL_SECONDS", "300")))
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
