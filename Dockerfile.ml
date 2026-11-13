# Training image: the ML pipeline (ml/) with its locked dependencies, CPU-only PyTorch, run as a non-root
# user. The same image trains on the server and as a GCP Cloud Run Job (docs/ml/implementation-plan.md,
# milestone 3). Two targets:
#
#   train  the published image (ghcr.io/csipapicsa/eek-ml-train): code + runtime dependencies only
#   test   train + pytest + tests/ml; the build runs the ML tests, so a broken image can't be built
#
#   docker build -f Dockerfile.ml --target test .
#   docker build -f Dockerfile.ml --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t eek-ml-train .
#   docker run --rm -v ./data:/data -e MLFLOW_TRACKING_URI=... eek-ml-train python -m ml.train --snapshot /data/snapshots/<name>
#
# Data lives in the /data volume: gameplay.db, snapshots/, embeddings/ (cache) and huggingface/ (model
# downloads). The embedding models are not baked in: the server ships the embeddings cache with the
# snapshot, so a training job normally never loads them.
FROM docker.io/library/python:3.15-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9 AS train

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# The big layer (PyTorch alone is several hundred MB), rebuilt only when the lock file changes. Every
# package is pinned with its hash; torch comes from the CPU-only index (the PyPI build bundles GPU libraries).
COPY requirements-ml.txt .
RUN pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-ml.txt

# The pipeline and the three game modules it reads gameplay.db with (stdlib only, no web app).
COPY server/__init__.py server/paths.py server/gameplay_log.py server/
COPY ml/ ml/

# Unprivileged user, same UID as the game image. It has a home directory because libraries (torch,
# huggingface) put small caches there.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin eek \
    && mkdir /data && chown eek /data
USER eek

ENV EEK_DATA_DIR=/data \
    EEK_ML_DATA_DIR=/data \
    HF_HOME=/data/huggingface \
    GIT_PYTHON_REFRESH=quiet
VOLUME /data

# The commit the image was built from. With no git inside, the pipeline tags every MLflow run with these,
# so a run can be rebuilt from its commit, its data hash and its image.
ARG GIT_SHA=dev
ENV EEK_VERSION=$GIT_SHA \
    EEK_ML_IMAGE=ghcr.io/csipapicsa/eek-ml-train:sha-$GIT_SHA
LABEL org.opencontainers.image.source="https://github.com/csipapicsa/emberisegelleneskartyak" \
      org.opencontainers.image.description="Emberiségellenes Kártyák: ML training pipeline (PyTorch CPU + MLflow)" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.revision=$GIT_SHA

# export -> validate -> train on /data/gameplay.db, if there's enough new data (ml/pipeline.toml).
CMD ["python", "-m", "ml.pipeline"]


FROM train AS test

USER root
# pytest and the rest of the dev tools, at the versions CI uses everywhere else.
COPY requirements-dev.txt .
RUN pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements-dev.txt -r requirements-ml.txt
COPY scripts/__init__.py scripts/make_sample_gameplay_db.py scripts/
COPY tests/__init__.py tests/
COPY tests/ml/ tests/ml/
USER eek
# --confcutdir: tests/conftest.py belongs to the web app (it patches server.game), which isn't in this image.
RUN python -m pytest -q -p no:cacheprovider --confcutdir=tests/ml tests/ml
