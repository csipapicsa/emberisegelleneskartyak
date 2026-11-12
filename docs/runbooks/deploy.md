# Runbook: releases, rollbacks and the move to a production container

How code and models reach production, how to undo it, and the one-time steps to move production from the
host service into its own container. Commands marked "as root" need `sudo -i` first (paste the commands
after the root prompt appears, not together with `sudo -i`).

## How a release works

```
code:   PR → CI (lint, unit, ML, browser tests against the image) → merge → :main → staging, automatically
                       → Promote workflow, manual approval → :prod → production
model:  nightly training → @challenger → staging's computer player
                       → python -m ml.promote (comparison + confirmation) → @champion → production's computer player
```

- **Staging** (`staging.emberisegelleneskartyak.hu`) follows `:main`: every merged commit that passed CI is
  live there within ~5 minutes. If the new container doesn't become healthy, it rolls back by itself.
- **Production** follows `:prod`, which only the Promote workflow moves, after an approval in GitHub.
  Production runs exactly the image CI tested (retagged, never rebuilt).
- **A restart ends the games in progress**: rooms live in memory. Release when nobody is playing:

  ```bash
  curl -s http://127.0.0.1:8010/api/rooms     # [] = nobody is in a game
  ```

## Day to day

**Release code to production:** GitHub → Actions → *Promote to production* → *Run workflow* (empty commit =
what `:main` is now, or a 7-character commit) → approve. The job summary shows the commit it replaced.

```bash
curl -s http://127.0.0.1:8010/healthz         # {"status":"ok","version":"<the promoted commit>"} within ~5 min
```

**Roll back code:** the same workflow with the previous commit (from the summary of the last run).

**Release a model:** `venv-ml/bin/python -m ml.promote` (see [../ml/pipeline.md](../ml/pipeline.md#promoting-a-model)).
**Roll back a model:** `venv-ml/bin/python -m ml.promote --rollback`. Both take effect within 5 minutes without a
restart.

**Is the computer player using the model?**

```bash
curl -s http://127.0.0.1:8010/healthz         # "computer_player": {"model": N, "random": 0}
curl -s http://127.0.0.1:8011/healthz         # the same for staging
journalctl CONTAINER_NAME=eek-model -n 20     # "loaded pick-model version N"
```

## Release the computer player on production (host service)

Production still runs on the host (`emberisegellenes.service`, from `/opt/emberisegellenes_kartyak`). The
computer player needs the model service to serve `champion` and to listen on `127.0.0.1:8021`, and the game
needs to know its address. As root, at a quiet time:

```bash
cd /opt/emberisegellenes_kartyak
git pull --ff-only                                      # as adminuser_developer, if not yet done
uid=$(id -u eek)
install -m 644 deploy/containers/eek-staging.network deploy/containers/eek-prod.network \
    deploy/containers/eek-model.container deploy/containers/eek-staging.container /etc/containers/systemd/users/$uid/
systemctl --user -M eek@ daemon-reload
systemctl --user -M eek@ restart eek-model.service && echo "MODEL SERVICE HEALTHY"
systemctl --user -M eek@ restart eek-staging.service
curl -s http://127.0.0.1:8021/healthz                   # "models": challenger and champion both "ok"

curl -s http://127.0.0.1:8010/api/rooms                 # wait for []
install -m 644 deploy/systemd/emberisegellenes.service.d/computer-player.conf /etc/systemd/system/emberisegellenes.service.d/
systemctl daemon-reload && systemctl restart emberisegellenes
curl -s http://127.0.0.1:8010/healthz                   # "computer_player": {"model": 0, "random": 0}
```

To take it back off: remove the drop-in, `daemon-reload`, restart (quiet time). The game then no longer
offers the computer player; nothing else changes.

## One-time: move production into its container

Production's data moves from the repository folder to `/srv/eek/prod`, the game runs as `eek-prod.container`
on the same address (`127.0.0.1:8010`, so nginx doesn't change), and the timers follow the data. Rehearsed
on 2026-11-12: the production image on a copy of the production databases served the same 17 editions and
played a full three-player browser round.

### 1. Prepare (any time, nothing stops)

As root:

```bash
cd /opt/emberisegellenes_kartyak
uid=$(id -u eek)
install -d -o eek -g eek -m 750 /srv/eek/prod /srv/eek/prod/database /srv/eek/backups/prod
( umask 027; cp /etc/emberisegellenes/privacy.env /etc/eek/prod.env ); chown root:eek /etc/eek/prod.env
usermod -aG eek adminuser_developer                      # the nightly training reads gameplay.db from there
install -m 644 deploy/containers/eek-prod.network deploy/containers/eek-prod.container /etc/containers/systemd/users/$uid/
install -m 644 deploy/systemd/eek-prod-db-backup.service deploy/systemd/eek-prod-db-backup.timer \
    deploy/systemd/eek-prod-print-cleanup.service deploy/systemd/eek-prod-print-cleanup.timer /etc/systemd/system/
install -d /etc/systemd/system/eek-ml-train.service.d
systemctl daemon-reload && systemctl --user -M eek@ daemon-reload
```

Then promote once (GitHub → *Promote to production*), so `:prod` exists, and pull it ahead of time:

```bash
cd / && sudo -u eek env XDG_RUNTIME_DIR=/run/user/$uid podman pull ghcr.io/csipapicsa/emberisegelleneskartyak:prod
```

### 2. Cut over (a quiet time; about two minutes without the site)

As root:

```bash
curl -s http://127.0.0.1:8010/api/rooms                  # must be []
systemctl stop emberisegellenes
cd /opt/emberisegellenes_kartyak
for f in analytics.db chat.db messages.db gameplay.db database/card_master.db database/card_seen.db; do
  sqlite3 "$f" ".backup '/srv/eek/prod/$f'"
done
chown -R eek:eek /srv/eek/prod
systemctl --user -M eek@ start eek-prod.service && echo "PRODUCTION CONTAINER HEALTHY"
curl -s http://127.0.0.1:8010/healthz                    # the promoted commit
```

Check the site in a browser (create a room, look at the packs), then make it permanent:

```bash
systemctl disable emberisegellenes
systemctl disable --now emberisegellenes-db-backup.timer emberisegellenes-print-cleanup.timer
systemctl enable --now eek-prod-db-backup.timer eek-prod-print-cleanup.timer
install -m 644 deploy/systemd/eek-ml-train.service.d/prod-container.conf /etc/systemd/system/eek-ml-train.service.d/
rm -f /etc/systemd/system/emberisegellenes.service.d/computer-player.conf   # the container sets it itself
systemctl daemon-reload
systemctl start eek-prod-db-backup.service && ls /srv/eek/backups/prod   # a first backup
```

### Roll back the cutover

As root: stop the container, copy the databases back (they may have new games), start the host service.

```bash
systemctl --user -M eek@ stop eek-prod.service
cd /opt/emberisegellenes_kartyak
for f in analytics.db chat.db messages.db gameplay.db database/card_seen.db; do
  sqlite3 "/srv/eek/prod/$f" ".backup '$f'"
done
systemctl enable --now emberisegellenes emberisegellenes-db-backup.timer emberisegellenes-print-cleanup.timer
```

After the move, the repository folder on the server is only a working copy: pulling it no longer changes
production. Production changes only through the Promote workflow.
