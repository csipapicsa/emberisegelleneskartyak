"""ml/cloud.py: staging, create-only upload, starting and watching the Cloud Run Job, with a fake HTTP session."""
from __future__ import annotations

import json

import pytest

from ml import cloud, export, features, validate
from scripts import make_sample_gameplay_db as sample

from .conftest import StemEncoder

EXECUTION = "projects/p/locations/europe-west4/jobs/eek-train/executions/eek-train-abc12"


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "gameplay.db"
    sample.generate(db, games=8, seed=3)
    folder, _ = export.export(db, tmp_path_factory.mktemp("snapshots"))
    assert all(r.ok for r in validate.validate_snapshot(folder, first_day=sample.FIRST_DAY))
    return folder


@pytest.fixture
def config(tmp_path):
    return cloud.CloudConfig(project="p", region="europe-west4", bucket="b", job="eek-train",
                             key_file=tmp_path / "key.json", mlflow_url="https://mlflow.example",
                             embeddings=["minilm", "e5"], models=["linear"], experiment="pick-model")


class Response:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code, self._body = status, body or {}
        self.ok = 200 <= status < 300
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeHttp:
    """Records requests; answers from a queue per method."""

    def __init__(self, posts=(), gets=()):
        self.posts, self.gets, self.calls = list(posts), list(gets), []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.posts.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.gets.pop(0)


def test_stage_lists_the_snapshot_and_complete_embeddings(snapshot, tmp_path):
    cache = tmp_path / "emb"
    objects = cloud.stage(snapshot, ["minilm", "e5"], cache, StemEncoder)
    prefix = f"cycles/{snapshot.name}"
    names = set(objects)
    assert f"{prefix}/snapshot/manifest.json" in names
    assert f"{prefix}/snapshot/validation.json" in names, "the job refuses unvalidated snapshots"
    rev = {k: features.EMBEDDING_MODELS[k].revision[:12] for k in ("minilm", "e5")}
    assert f"{prefix}/embeddings/minilm/{rev['minilm']}.parquet" in names
    assert f"{prefix}/embeddings/e5/{rev['e5']}.parquet" in names
    # The uploaded cache must cover every text, so the job never has to load a model (it can't write there).
    _, tables = export.read_snapshot(snapshot)
    texts = {c["text_id"]: c["text"] for c in tables["card_texts"]}

    def no_model(model):
        raise AssertionError("the staged cache should already hold every text")

    features.embed(texts, features.EMBEDDING_MODELS["e5"], cache, no_model)


def test_upload_is_create_only_and_skips_existing_objects(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"1")
    b.write_bytes(b"2")
    http = FakeHttp(posts=[Response(200), Response(412)])
    done = cloud.upload(http, "bucket", {"x/a": a, "x/b": b})
    assert done == {"uploaded": ["x/a"], "existing": ["x/b"]}
    method, url, kwargs = http.calls[0]
    assert url == "https://storage.googleapis.com/upload/storage/v1/b/bucket/o"
    assert kwargs["params"] == {"uploadType": "media", "name": "x/a", "ifGenerationMatch": "0"}
    assert kwargs["data"] == b"1"


def test_upload_error_stops(tmp_path):
    (tmp_path / "a").write_bytes(b"1")
    with pytest.raises(RuntimeError, match="HTTP 403"):
        cloud.upload(FakeHttp(posts=[Response(403, {"error": "denied"})]), "bucket", {"a": tmp_path / "a"})


def test_job_overrides_point_the_job_at_the_uploaded_snapshot(config):
    overrides = cloud.job_overrides("2026-11-07_abc", config, register=False)
    container = overrides["containerOverrides"][0]
    assert container["args"] == ["-m", "ml.train", "--snapshot", "/gcs/cycles/2026-11-07_abc/snapshot",
                                 "--embeddings", "minilm,e5", "--models", "linear", "--experiment", "pick-model",
                                 "--no-register"]
    assert container["env"] == [{"name": "EEK_ML_DATA_DIR", "value": "/gcs/cycles/2026-11-07_abc"}]
    assert "--no-register" not in cloud.job_overrides("s", config, register=True)["containerOverrides"][0]["args"]


def test_run_job_returns_the_execution(config):
    http = FakeHttp(posts=[Response(200, {"name": "operations/1", "metadata": {"name": EXECUTION}})])
    assert cloud.run_job(http, config, {"containerOverrides": []}) == EXECUTION
    method, url, kwargs = http.calls[0]
    assert url == "https://run.googleapis.com/v2/projects/p/locations/europe-west4/jobs/eek-train:run"
    assert kwargs["json"] == {"overrides": {"containerOverrides": []}}
    with pytest.raises(RuntimeError, match="HTTP 403"):
        cloud.run_job(FakeHttp(posts=[Response(403)]), config, {})


def test_wait_for_polls_until_done():
    sleeps = []
    http = FakeHttp(gets=[Response(200, {"runningCount": 1}),
                          Response(200, {"completionTime": "t", "succeededCount": 1, "logUri": "L"})])
    assert cloud.wait_for(http, EXECUTION, poll_seconds=5, sleep=sleeps.append)["logUri"] == "L"
    assert sleeps == [5]
    assert http.calls[0][1] == f"https://run.googleapis.com/v2/{EXECUTION}"


def test_wait_for_reports_failures_and_timeouts():
    failed = FakeHttp(gets=[Response(200, {"completionTime": "t", "failedCount": 1, "logUri": "L"})])
    with pytest.raises(RuntimeError, match="eek-train-abc12 failed; logs: L"):
        cloud.wait_for(failed, EXECUTION, sleep=lambda s: None)
    running = FakeHttp(gets=[Response(200, {"runningCount": 1})] * 3)
    with pytest.raises(RuntimeError, match="still running"):
        cloud.wait_for(running, EXECUTION, poll_seconds=10, timeout=20, sleep=lambda s: None)


def test_load_config_reads_the_cloud_and_train_sections(tmp_path):
    config = cloud.load_config()  # the real ml/pipeline.toml
    assert config.job == "eek-train" and config.region == "europe-west4"
    assert config.embeddings == ["minilm", "e5", "granite"]
    assert not str(config.key_file).startswith("~")
    broken = tmp_path / "p.toml"
    broken.write_text('[cloud]\nproject = "p"\n')
    with pytest.raises(ValueError, match="missing region"):
        cloud.load_config(broken)
