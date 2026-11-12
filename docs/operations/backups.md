# Database backups

## What is backed up

`scripts/backup_databases.py` backs up every `.db` file in the data folder and in `database/`: the runtime
databases (`analytics.db`, `chat.db`, `messages.db`, `gameplay.db`), the uploaded card database
`database/card_master.db` (saved as `database__card_master--<timestamp>.db`) and the dealt-card counts. It uses
SQLite's online backup API, so the copies are consistent while the site is running.

## Where, and for how long

- Folder: `backups/` in the data folder (or `--backup-dir`).
- File name: `<database>--<UTC timestamp>.db`.
- The folder and the files are readable by the service user only.
- Retention: 30 days. The next daily run deletes older backups.
- `backups/` is not in Git: these are local copies for quick restores, not an archive.

## Daily run

A systemd timer runs at 03:05 and catches up on a missed run at the next boot. Install (as root):

```bash
install -m 644 /opt/emberisegellenes_kartyak/deploy/systemd/emberisegellenes-db-backup.service \
               /opt/emberisegellenes_kartyak/deploy/systemd/emberisegellenes-db-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now emberisegellenes-db-backup.timer
```

Check: `systemctl list-timers emberisegellenes-db-backup.timer`.
A backup by hand: `/usr/bin/python3 /opt/emberisegellenes_kartyak/scripts/backup_databases.py`.

Once production runs in its container, `eek-prod-db-backup.timer` takes over: it backs up `/srv/eek/prod` into
`/srv/eek/backups/prod` as the `eek` user ([../runbooks/deploy.md](../runbooks/deploy.md)).

## Restoring

Stop the service, set the current database aside, and replace only the affected `.db` file with the chosen
backup; then start the service again. Always take a fresh backup of the current state first.

These local backups don't protect against losing the server or the account. If that risk matters, add a
separate, encrypted, off-server copy.
