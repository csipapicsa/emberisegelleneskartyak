# Production image: the FastAPI app with its runtime dependencies, run as a non-root user.
# Runtime data (SQLite databases, the uploaded card database, generated PDFs) lives in the /data volume,
# so replacing the image never touches it. Works with docker and podman.
#
#   docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t emberisegellenes .
#   docker run -p 127.0.0.1:8010:8010 -v ./data:/data emberisegellenes
#
# The base image is pinned by digest, so a rebuild of the same commit uses the same Python.
FROM docker.io/library/python:3.11-slim-bookworm@sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# Dependencies first: this layer is rebuilt only when requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Only what the app needs at runtime: no tests, docs or deploy files.
COPY config.json .
COPY server/ server/
COPY static/ static/
COPY scripts/ scripts/

# Unprivileged user. The fixed UID lets the host give the data volume to this user.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin eek \
    && mkdir /data && chown eek /data
USER eek

ENV EEK_DATA_DIR=/data
VOLUME /data

# The commit the image was built from; /healthz reports it so a deploy can check what's live.
ARG GIT_SHA=dev
ENV EEK_VERSION=$GIT_SHA
LABEL org.opencontainers.image.source="https://github.com/csipapicsa/emberisegelleneskartyak" \
      org.opencontainers.image.description="Emberiségellenes Kártyák: multiplayer party card game (FastAPI)" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.revision=$GIT_SHA

EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/healthz', timeout=4)"]

# 0.0.0.0 inside the container; the host decides what is exposed (e.g. -p 127.0.0.1:8010:8010 behind nginx).
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8010"]
