# Containers: staging and production

The app runs as a rootless podman container under a dedicated `eek` system user (no login, no sudo),
managed by systemd through [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).
Deploys are pull-based: CI publishes images to GHCR, and the server pulls them. GitHub has no access to
the server.

| Environment | Image tag | Updated by | Port | Data |
|---|---|---|---|---|
| staging | `:main` | `podman auto-update`, every 5 minutes | 127.0.0.1:8011 | `/srv/eek/staging` |
| production | `:prod` | promote workflow (planned) | 127.0.0.1:8010 | `/srv/eek/prod` |

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

## Day to day (as root)

```bash
systemctl --user -M eek@ status eek-staging.service
journalctl --user-unit=eek-staging.service -f              # app logs
journalctl --user-unit=podman-auto-update.service          # what auto-update pulled, or rolled back
cd / && sudo -u eek env XDG_RUNTIME_DIR=/run/user/$(id -u eek) podman auto-update --dry-run   # newer image waiting?
```
