# MLflow tracking server and model registry, with its login enabled.
# The official image plus the two packages its basic-auth app needs. Data (the run/registry database,
# the user database and the artifacts) lives in the /data volume.
#
#   podman build -t eek-mlflow deploy/mlflow
#   podman run -p 127.0.0.1:5000:5000 -v ./mlflow-data:/data -e MLFLOW_AUTH_ADMIN_PASSWORD=... eek-mlflow
#
# The base image is pinned by digest, so a rebuild gives the same MLflow.
FROM ghcr.io/mlflow/mlflow:v3.17.2@sha256:2cc40b3208991940d824d04f339833a0b69507569012aa5ec545690608602fc0

LABEL org.opencontainers.image.source="https://github.com/csipapicsa/emberisegelleneskartyak" \
      org.opencontainers.image.description="MLflow tracking server with login, for the computer player"

# MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false: no background job workers (seven processes, ~1.5 GB of RAM).
# They serve MLflow's GenAI features (trace scoring and archival), which this project doesn't use.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    MLFLOW_AUTH_CONFIG_PATH=/etc/mlflow/basic_auth.ini \
    MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-deps --require-hashes -r /tmp/requirements.txt && pip check && rm /tmp/requirements.txt

COPY basic_auth.ini /etc/mlflow/basic_auth.ini

VOLUME /data
EXPOSE 5000

# /health answers without a login.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4)"]

# Runs, metrics and the registry in SQLite; artifacts (models, plots) as files, served by MLflow itself,
# so clients (a laptop, the cloud training job) only need the URL and a login, never storage credentials.
# One worker: a handful of users (Gergo, the training jobs, later the model service).
# MLFLOW_ALLOWED_HOSTS: the Host headers to accept (protection against DNS rebinding); set per deployment.
CMD ["sh", "-c", "exec mlflow server --app-name basic-auth --host 0.0.0.0 --port 5000 --workers 1 --backend-store-uri sqlite:////data/mlflow.db --serve-artifacts --artifacts-destination /data/artifacts --allowed-hosts \"${MLFLOW_ALLOWED_HOSTS:-localhost:5000,127.0.0.1:5000}\""]
