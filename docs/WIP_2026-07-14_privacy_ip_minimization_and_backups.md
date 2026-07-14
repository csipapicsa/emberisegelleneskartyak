# WIP — Privacy, IP-kezelés, adatmegőrzés és adatbázis-mentések (2026-07-14)

## Status

Az implementáció `main`-ben van és GitHubra fel lett töltve:

```text
fa617bb fix: retain admin analytics event details
96ca459 feat: add daily database backups
d828a6d feat: publish privacy notice and retention rules
ba78dec feat: minimise IP-based data processing
```

Az adatkezelési oldal a futó szerveren elérhető:

```text
https://emberisegelleneskartyak.hu/adatkezeles
```

A legutóbbi statisztikai részlet-visszaállítás (`fa617bb`) csak a service
újraindítása után lesz éles:

```bash
sudo systemctl restart emberisegellenes.service
```

Az első helyi SQLite-mentés elkészült a `backups/` könyvtárban: `analytics.db`,
`cards.db`, `cards_safety_2026_07_11.db`, `chat.db` és `messages.db`.

## Mi készült?

### 1. IP-alapú statisztika és kártyaötlet-szavazás

Új közös segéd: `server/privacy.py`.

- `EEK_PRIVACY_HMAC_SECRET` környezeti titokkal HMAC-SHA-256 azonosítót képez.
- A `purpose` a bemenet része, így a kártyaötlet-szavazás és a belső
  statisztika nem használ közös IP-azonosítót.
- A kártyaötlet-szavazás sémája a korábbi `voter_ip` oszlop helyett
  `voter_hash` oszlopot használ.
- Induláskor a meglévő nyers-IP-s szavazatok HMAC-os formára migrálódnak. Ez
  csak konfigurált titok mellett futhat.

A `server/main.py` `_client_ip()` függvénye a nginx által felülírt
`X-Real-IP` fejlécet használja; a kliens által küldött `X-Forwarded-For`
fejlécet nem bízza meg. A kapcsolódó nginx konfiguráció a továbbított címláncot
felülírja a tényleges klienscímmel.

### 2. Analitika és admin statisztika

`server/analytics.py`:

- A látogató-azonosító most célhoz kötött HMAC; nyers IP-t nem ír az
  `analytics.db`-be.
- Minden analitikai esemény 90 nap után törlődik.
- Az eseményekhez megmarad a szobakód, játékos által megadott név és legfeljebb
  200 karakteres user-agent; ezek szükségesek az `/admin/stats` legutóbbi
  esemény táblázatához.
- Az admin táblázat oszlopai: időpont, esemény, szoba, felhasználó,
  játékoslétszám.

### 3. Nyilvános adatkezelési tájékoztató

Új oldal: `static/adatkezeles.html`, route: `/adatkezeles`.

Tartalma többek között:

- adatkezelő: Győri Gergő; kapcsolat:
  `gergo.gyori.project@gmail.com`;
- Contabo mint tárhelyszolgáltató/adatfeldolgozó;
- játékmenet, chat, kapcsolatfelvétel, kártyaötlet, szavazás, belső
  statisztika és webszerver-naplók célja/jogalapja/megőrzése;
- WebRTC/coturn hangfunkció és `localStorage` használata;
- érintetti jogok és NAIH-panasz lehetősége.

Az oldal linkje bekerült a főoldalra, a kapcsolatfelvételi ablakba, a
kártyaötlet- és a kártyaszavazás-oldalra.

### 4. Megőrzési idők

| Adat | Megőrzés | Implementáció |
|---|---:|---|
| Analitikai események | 90 nap | `server/analytics.py` automatikus törlés |
| Kapcsolatfelvételi üzenetek | 12 hónap | `server/messages_db.py` automatikus törlés |
| Nyilvános üzenőfal | 30 nap | meglévő törlés `get_chat_messages()` közben |
| Szobachat | a szoba megszűnéséig | szobatörléskor, illetve induláskor törlődik |
| Kártyaötlet-szavazati HMAC | 5 év | `purge_expired_data()` |
| Kártyaötlet szövege | korlátlan | játéktartalom; nem kapcsolódik beküldői névhez vagy IP-hez |
| nginx access/error log | 14 nap | telepítendő logrotate-konfiguráció |

### 5. Helyi adatbázis-mentések

Új script: `scripts/backup_databases.py`.

- Minden, projektgyökérben lévő `.db` fájlt ment.
- A SQLite online backup API-t használja, így élő adatbázisról is konzisztens
  mentést készít.
- A mentések helye `backups/`; ez Gitből ki van zárva.
- A mentés fájlneve például `analytics--20260714T124038Z.db`.
- A script minden futáskor törli a 30 napnál régebbi mentéseket.
- A mentés fájljai `0600`, a könyvtár `0700` jogosultságú.
- Egy lock megakadályozza, hogy két mentés egyszerre fusson.

Napi 03:05-ös futáshoz a következő systemd fájlok készültek:

- `systemd/emberisegellenes-db-backup.service`
- `systemd/emberisegellenes-db-backup.timer`

Telepítés:

```bash
sudo install -m 644 /opt/emberisegellenes_kartyak/systemd/emberisegellenes-db-backup.service /etc/systemd/system/
sudo install -m 644 /opt/emberisegellenes_kartyak/systemd/emberisegellenes-db-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now emberisegellenes-db-backup.timer
```

Részletes leírás: `docs/BACKUPS.md`.

## Incidens és korlátozás

Az első privacy-migráció futtatásakor a korábbi `analytics.db` eseményekből
törlődtek az `ip_hash`, `username`, `room_code` és `user_agent` értékek. Ez
hibás, túl agresszív migráció volt: az egyedi látogatói statisztikák és a régi
admin esemény-részletek elvesztek.

Az `analytics.db` futásidejű fájl, `.gitignore` kizárja, és nem volt róla
helyi vagy GitHub-mentés. Ezért a törölt történeti IP-hashek és játékosnevek
nem állíthatók vissza. A javított kód már nem nulláz történeti analitikai
mezőket, és az új események ismét tárolják a statoldalhoz szükséges neveket és
szobakódokat.

Ez az incidens indokolja a fenti 30 napos, napi adatbázis-mentést. A helyi
mentés szervervesztés ellen önmagában nem véd; ahhoz külön, szerveren kívüli
és titkosított mentés szükséges.

## Ellenőrzés

```bash
venv/bin/pytest -q
# 243 passed a backup-implementáció után

venv/bin/pytest -q tests/test_analytics_privacy.py
# 2 passed a név/szobakód-visszaállítás után
```

Az első kézi mentés sikeresen lefutott:

```text
Created 5 backup(s); removed 0 expired backup(s).
```

## Még szükséges üzemeltetői lépések

- [ ] `sudo systemctl restart emberisegellenes.service` — a `fa617bb` név- és
  szobakód-visszaállítás élesítéséhez.
- [ ] A backup systemd service és timer telepítése/futtatása a fenti négy
  paranccsal.
- [ ] `nginx_emberisegellenes.conf` és
  `nginx_emberisegellenes.logrotate` telepítése, majd nginx reload; enélkül a
  14 napos nginx-log rotáció még nincs élesben.
- [ ] Havi vagy negyedéves próbavisszaállítás egy másolatból, hogy a mentés
  ténylegesen használható maradjon.
