# Adatbázis-mentések

## Mit ment a rendszer?

A `scripts/backup_databases.py` minden, a projekt gyökerében és a
`database/` könyvtárban lévő `.db` fájlról mentést készít. Jelenleg ez például
a `cards.db`, `messages.db`, `chat.db`, `analytics.db`, a biztonsági
kártyaadatbázis és a `database/card_master.db` (ennek mentése
`database__card_master--<időbélyeg>.db` néven készül). A mentés a SQLite
online backup API-ját használja, ezért futó weboldal mellett is konzisztens.

## Hova és meddig?

- Könyvtár: `backups/` a projekt gyökerében.
- Fájlnév: `<adatbázisnév>--<UTC-időbélyeg>.db`.
- A könyvtár és a mentések jogosultsága csak a szolgáltatás felhasználójának
  ad hozzáférést.
- Megőrzés: 30 nap. A következő napi futás törli a 30 napnál régebbi
  mentéseket.
- A `backups/` Gitből ki van zárva: ez nem GitHub-mentés, hanem helyi,
  gyors visszaállítási lehetőség.

## Napi futás

A systemd timer napi 03:05-kor fut, és kimaradt futást is pótol a következő
induláskor. Telepítés:

```bash
sudo install -m 644 /opt/emberisegellenes_kartyak/systemd/emberisegellenes-db-backup.service /etc/systemd/system/emberisegellenes-db-backup.service
sudo install -m 644 /opt/emberisegellenes_kartyak/systemd/emberisegellenes-db-backup.timer /etc/systemd/system/emberisegellenes-db-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now emberisegellenes-db-backup.timer
```

Ellenőrzés:

```bash
systemctl list-timers emberisegellenes-db-backup.timer
```

Kézi mentés:

```bash
/usr/bin/python3 /opt/emberisegellenes_kartyak/scripts/backup_databases.py
```

## Visszaállítás

Előbb állítsd le a szolgáltatást, majd mentsd félre az aktuális adatbázist és
csak az érintett `.db` fájlt cseréld ki egy kiválasztott mentésre. Ezután
indítsd újra a szolgáltatást. A visszaállítás előtt mindig készíts egy új
mentést az aktuális állapotról is.

Ez a helyi mentés nem véd szerver- vagy fiókszintű adatvesztés ellen. Ha ez
fontos kockázat, kell egy külön, titkosított, szerveren kívüli mentés is.
