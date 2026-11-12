# Containers: staging, production and MLflow

The app runs as a rootless podman container under a dedicated `eek` system user (no login, no sudo),
managed by systemd through [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).
Deploys are pull-based: CI publishes images to GHCR, and the server pulls them. GitHub has no access to
the server.

| Environment | Image tag | Updated by | Port | Data |
|---|---|---|---|---|
| staging | `:main` | `podman auto-update`, every 5 minutes | 127.0.0.1:8011 | `/srv/eek/staging` |
| production | `:prod` | promote workflow (planned) | 127.0.0.1:8010 | `/srv/eek/prod` |
| MLflow | `eek-mlflow:<mlflow version>` | by hand (below) | 127.0.0.1:5000 | `/srv/eek/mlflow` |

When auto-update finds a new image, it restarts the unit. The unit counts as started only once the
container is healthy (`Notify=healthy`), so a broken image fails the restart and auto-update rolls back to
the previous one.

## Files

- `eek-staging.container`: the staging unit, installed as `/etc/containers/systemd/users/<uid>/`.
  Root-owned, so the service user can't change its own configuration.
- `podman-auto-update.timer.d/10-every-5-min.conf`: makes auto-update check every 5 minutes instead of
  daily. Installed into `/etc/systemd/user/podman-auto-update.timer.d/`.
- `../nginx/staging.emberisegelleneskartyak.hu.conf`: HTTPS and a password in front of staging.
  `/healthz` stays public so CI can check which commit is live.
- `eek-model.container` + `eek-staging.network` + `eek-prod.network`: the model service for the computer
  player (`ml/serve.py`, the ML training image): `challenger` for staging, `champion` for production, on a
  network shared with each (staging and production can't reach each other).
- `eek-prod.container`: production, following `:prod` (moved only by the Promote workflow, after an
  approval). Moving production from the host service into it, releases and rollbacks:
  [../../docs/runbooks/deploy.md](../../docs/runbooks/deploy.md).
- `eek-mlflow.container`: MLflow tracking server and model registry, image from `../mlflow/`
  (built and tested by `.github/workflows/mlflow-image.yml`). With `../nginx/mlflow.emberisegelleneskartyak.hu.conf`
  and the daily database backup `../systemd/eek-mlflow-backup.{service,timer}`.

## First-time setup (as root)

```bash
# Service user with its own subordinate UID range; linger starts its services at boot.
useradd --system --create-home --home-dir /var/lib/eek --shell /usr/sbin/nologin eek
usermod --add-subuids 231072-296607 --add-subgids 231072-296607 eek   # a range no other user has
loginctl enable-linger eek
install -d -o eek -g eek -m 750 /srv/eek /srv/eek/staging /srv/eek/prod

# Secrets: one file per environment, readable by eek only
install -d -o root -g eek -m 750 /etc/eek
# /etc/eek/staging.env (root:eek 0640): EEK_PRIVACY_HMAC_SECRET, EEK_ADMIN_STATS_KEY, see ../privacy.env.example
```

## Install staging

```bash
uid=$(id -u eek)

# The card database (copy it with sqlite3 .backup, not cp, so the copy is consistent)
install -d -o eek -g eek -m 750 /srv/eek/staging/database
install -o eek -g eek -m 640 card_master.db /srv/eek/staging/database/card_master.db

# Unit and auto-update timer
install -d -m 755 /etc/containers/systemd/users/$uid /etc/systemd/user/podman-auto-update.timer.d
install -m 644 deploy/containers/eek-staging.container /etc/containers/systemd/users/$uid/
install -m 644 deploy/containers/podman-auto-update.timer.d/10-every-5-min.conf /etc/systemd/user/podman-auto-update.timer.d/
systemctl --user -M eek@ daemon-reload
systemctl --user -M eek@ start eek-staging.service       # first start pulls the image; ~30 s until healthy
systemctl --user -M eek@ enable --now podman-auto-update.timer
curl -s http://127.0.0.1:8011/healthz                   # {"status":"ok","version":"<commit>"}

# nginx: password, site, HTTPS
read -rsp 'Staging password: ' p; echo
printf 'staging:%s\n' "$(openssl passwd -apr1 "$p")" > /etc/nginx/eek-staging.htpasswd; unset p
chown root:nginx /etc/nginx/eek-staging.htpasswd; chmod 640 /etc/nginx/eek-staging.htpasswd
install -m 644 deploy/nginx/staging.emberisegelleneskartyak.hu.conf /etc/nginx/conf.d/
nginx -t && systemctl reload nginx
certbot --nginx -d staging.emberisegelleneskartyak.hu
```

## Computer player on staging (as root)

The model service loads `pick-model@challenger` from MLflow as the `reader` user, and the staging game calls it
at `http://eek-model:8020` (set in `eek-staging.container`). Without it the computer player plays at random.

```bash
uid=$(id -u eek)
install -d -o eek -g eek -m 750 /srv/eek/model                 # Hugging Face cache

read -rsp 'MLflow reader password: ' p; echo
( umask 027
  printf 'MLFLOW_TRACKING_URI=https://mlflow.emberisegelleneskartyak.hu\nMLFLOW_TRACKING_USERNAME=reader\nMLFLOW_TRACKING_PASSWORD=%s\n' \
    "$p" > /etc/eek/model.env ); unset p
chown root:eek /etc/eek/model.env

install -m 644 deploy/containers/eek-staging.network deploy/containers/eek-model.container \
    deploy/containers/eek-staging.container /etc/containers/systemd/users/$uid/
systemctl --user -M eek@ daemon-reload
systemctl --user -M eek@ start eek-model.service       # first start: image + embedding model, a few minutes
systemctl --user -M eek@ restart eek-staging.service   # joins the network
journalctl --user-unit=eek-model.service -n 20         # "loaded pick-model version N (e5)"
```

Is the computer player really using the model? Its random fallback keeps games working when the service
is down, so look at the counts after a game (`random` should stay at 0):

```bash
curl -s http://127.0.0.1:8011/healthz        # {"status":"ok", ..., "computer_player":{"model":12,"random":0}}
journalctl CONTAINER_NAME=eek-model -n 20    # the service's own log ("loaded pick-model version N")
```

Check from the server that a whole worst-case turn stays far inside the shortest timer (15 s), with brand-new
cards and 5 games at once:

```bash
cd / && sudo -u eek env XDG_RUNTIME_DIR=/run/user/$uid podman exec eek-model \
    python -m ml.serve_bench --url http://127.0.0.1:8020 --games 5 --rounds 10
```

## Day to day (as root)

```bash
systemctl --user -M eek@ status eek-staging.service
journalctl --user-unit=eek-staging.service -f              # app logs
journalctl --user-unit=podman-auto-update.service          # what auto-update pulled, or rolled back
cd / && sudo -u eek env XDG_RUNTIME_DIR=/run/user/$(id -u eek) podman auto-update --dry-run   # newer image waiting?
```

## MLflow

Experiment tracking and the model registry for the computer player ([docs/ml/roadmap.md](../../docs/ml/roadmap.md)).
The official image plus MLflow's login (`--app-name basic-auth`); runs, registry and users in SQLite, artifacts
as files, all in `/srv/eek/mlflow`. MLflow serves the artifacts itself, so a client (laptop, cloud job, model
service) needs only the URL and a login:

```bash
export MLFLOW_TRACKING_URI=https://mlflow.emberisegelleneskartyak.hu
export MLFLOW_TRACKING_USERNAME=trainer MLFLOW_TRACKING_PASSWORD=...
```

| User | For | Can |
|---|---|---|
| `admin` | Gergo | everything, including users |
| `trainer` | training pipelines, the GCP job | log runs, register models, move the `champion` alias |
| `reader` | the model service (milestone 4) | read everything, load models |

Every logged-in user can read every experiment (`default_permission = READ` in `../mlflow/basic_auth.ini`);
whoever creates an experiment manages it. MLflow lets any user create a new, empty experiment, `reader` included.

The background job workers are switched off in the image (`MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false`): they serve
MLflow's GenAI features and cost about 1.5 GB of RAM. The server uses about 500 MB.

### Install (as root)

```bash
uid=$(id -u eek)
install -d -o eek -g eek -m 750 /srv/eek/mlflow /srv/eek/backups

# Secrets (see ../mlflow.env.example). The admin password is read only on the first start.
( umask 027
  printf 'MLFLOW_AUTH_ADMIN_PASSWORD=%s\nMLFLOW_FLASK_SERVER_SECRET_KEY=%s\n' \
    "$(openssl rand -base64 24)" "$(openssl rand -base64 32)" > /etc/eek/mlflow.env )
chown root:eek /etc/eek/mlflow.env

# Container
install -m 644 deploy/containers/eek-mlflow.container /etc/containers/systemd/users/$uid/
systemctl --user -M eek@ daemon-reload
systemctl --user -M eek@ start eek-mlflow.service      # first start pulls the image (~1 GB)
curl -s http://127.0.0.1:5000/health                  # OK

# nginx and HTTPS. No nginx password: MLflow asks for its own login.
install -m 644 deploy/nginx/mlflow.emberisegelleneskartyak.hu.conf /etc/nginx/conf.d/
nginx -t && systemctl reload nginx
certbot --nginx -d mlflow.emberisegelleneskartyak.hu

# Daily backup of the databases at 03:15, to /srv/eek/backups/mlflow (30 days)
install -m 644 deploy/systemd/eek-mlflow-backup.service deploy/systemd/eek-mlflow-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now eek-mlflow-backup.timer
systemctl start eek-mlflow-backup.service && ls /srv/eek/backups/mlflow
```

Then the two other users, from any shell (keep the printed passwords in a password manager):

```bash
read -rsp 'MLflow admin password: ' A; echo     # sudo grep ADMIN /etc/eek/mlflow.env
for u in trainer reader; do
  p=$(openssl rand -base64 18)
  curl -sf -u "admin:$A" -H 'Content-Type: application/json' -d "{\"username\":\"$u\",\"password\":\"$p\"}" \
    https://mlflow.emberisegelleneskartyak.hu/api/2.0/mlflow/users/create >/dev/null && echo "$u: $p"
done; unset A p
```

Change a password: `PATCH /api/2.0/mlflow/users/update-password` with `{"username": ..., "password": ...}`,
as `admin`.

### Upgrade MLflow

New versions can migrate the database, so: in one PR, bump the `FROM` line in `../mlflow/Dockerfile` and the
tag in `eek-mlflow.container` (CI checks they match). After the merge and the published image:

```bash
systemctl start eek-mlflow-backup.service                          # fresh backup first
install -m 644 deploy/containers/eek-mlflow.container /etc/containers/systemd/users/$(id -u eek)/
systemctl --user -M eek@ daemon-reload
systemctl --user -M eek@ restart eek-mlflow.service
journalctl --user-unit=eek-mlflow.service -n 50                   # migration messages, errors
```

Rollback: put the old container file back and copy the backup over `/srv/eek/mlflow/mlflow.db` (service stopped).
