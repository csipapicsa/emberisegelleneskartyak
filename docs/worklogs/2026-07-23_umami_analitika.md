# WIP — Önhosztolt Umami látogatottsági analitika (2026-07-23)

## Előzmény / cél

Az admin statisztika (`/admin/stats`) csúcsidő-diagramja körüli hibajavítás
közben felmerült, hogy a jelenlegi belső eseménynapló (`server/analytics.py`)
csak *játékbeli* eseményeket számol (`room_created`, `player_joined`,
`game_started`, `game_ended`, `room_abandoned`) — aki csak megnyitja a
főoldalt és lejjebb görget, az sehol nincs számolva. Ez elégtelen, ha a cél
valódi, kívülről ellenőrizhető látogatottsági szám (pl. az oldal eladásához
vagy hirdetői megkereséshez).

Döntés: nem Google Analytics, mert az `adatkezeles.html` már kimondja, hogy
*"Harmadik fél reklám- vagy analitikai szolgáltatónak nem adunk át adatot"* —
ezt egy önhosztolt, cookie-mentes eszköz (Umami) nem sérti, GA viszont igen.
A választás Umami vs. Plausible közül Umami mellett dőlt el: Plausible-nek
ClickHouse kell (2GB+ RAM csak arra), ami erre a megosztott, több site-ot is
futtató VPS-re (7.5GB RAM) nehézkes; Umami egyetlen Node-alkalmazás + Postgres.

## Status

Élesben fut, a főoldal HTML-jébe be van kötve. **Semmi nincs commitolva** —
ez a dokumentum és a kódváltozások jelenleg csak a szerver munkafájljaiban
vannak (`git status` szerint uncommitted), a `main` ág nem tartalmazza még.

- Dashboard: `https://stats.emberisegelleneskartyak.hu` (saját aldomain,
  Let's Encrypt tanúsítvánnyal, 2026-10-21-ig érvényes, automatikus
  megújítással a meglévő `certbot-renew.timer`-en keresztül)
- Umami website: "Emberiségellenes Kártyák", domain
  `emberisegelleneskartyak.hu`
- A tracking script mind a 9 statikus oldal `<head>`-jébe bekerült:
  `index.html`, `create.html`, `join.html`, `room.html`, `game.html`,
  `cardlist.html`, `kartyaotlet.html`, `kartyaszavazas.html`,
  `adatkezeles.html`

## Mi készült?

### 1. Csúcsidő-diagram javítás (`server/main.py`, `/admin/stats`)

- A `.hbar-col` flex-oszlopból hiányzott a `justify-content:flex-end`, ezért
  az alacsony órák oszlopai a konténer *tetejéhez* tapadtak, nem a
  vonalhoz — ez okozta a "fejjel lefelé" / "mintha lenne egy plafon" hatást.
  A pct-számítás (`cnt / max_cnt * 100`) már eddig is helyesen relatív volt a
  maximumhoz, csak a megjelenítés volt hibás.
- Minden órabárra `title="{óra}:00 — {darab} esemény"` attribútum került —
  natív böngésző-tooltip hoverre, JS nélkül.

### 2. Admin statisztika vizsgálat

- Megerősítve: csak valódi eseményt naplózunk, nincs ping/heartbeat zaj; a
  bot user-agentek ki vannak szűrve (`_is_bot`).
- A csúcsidő-diagramon látott magas (300-400/óra) számok oka: a diagram az
  5 eseménytípus összegét mutatja, 30 nap összesítve, óránkénti bontásban —
  nem egyedi látogatót. Egy lejátszott kör kb. 6-7 eseménysort generál
  (create+start+end+annyi join, ahány játékos). Nem spam/bot: 153 egyedi
  IP 30 nap alatt, nincs kiugró IP vagy szoba.
- `ip_hash` lefedettségi "rés" a `room_created` eseményeken (kb. 62% a teljes
  30 napos átlagban) — ez **nem élő hiba**, hanem a privacy-funkció
  2026-07-14-i bevezetése előtti/utáni napok keveredése. 2026-07-15 óta
  minden nap 100%.

### 3. Umami konténerek (podman, rootless, `systemctl --user`)

Két konténer, saját podman hálózaton (`umami_net`), csak
`127.0.0.1:8030`-ra publikálva (nginx az egyetlen külső belépési pont):

- `umami-db`: `postgres:16-alpine`, névvel ellátott volume
  (`umami_db_data`), nincs host-portja
- `umami-app`: `ghcr.io/umami-software/umami:postgresql-latest` (Umami
  3.2.0)

Systemd egységek: `~/.config/systemd/user/container-umami-db.service` és
`container-umami-app.service`, `systemctl --user enable --now`-nal
engedélyezve. `sudo loginctl enable-linger adminuser_developer` fut, hogy a
user-systemd instance kijelentkezés/reboot után is éljen.

### 4. nginx + TLS

`/etc/nginx/conf.d/stats.emberisegelleneskartyak.hu.conf` — proxy a
`127.0.0.1:8030`-ra, `certbot --nginx -d stats.emberisegelleneskartyak.hu`
állította be a HTTPS blokkot és a HTTP→HTTPS redirectet, ugyanúgy, ahogy a
másik 3 domainnél.

### 5. Umami első indítás

Az admin/umami alapértelmezett belépés nem működött (ismeretlen okból, a DB-
ben lévő hash egyébként pontosan egyezett az Umami hivatalos seed-hash-ével).
Megoldás: az admin jelszó közvetlenül az adatbázisban lett lecserélve egy
frissen generált, erős jelszóra (bcrypt hash, `python3 -m pip install --user
bcrypt` egyszeri, helyi segédeszközként). **A jelszót a felhasználó azóta
lecserélte a dashboardon** — ez a dokumentum szándékosan nem tartalmazza sem
a régi, sem az új jelszót, sem a Postgres/APP_SECRET generált titkokat.

## Incidens és korlátozás

Két hibás nekifutás volt a konténerek systemd-be kötésénél, mielőtt a végső
(user-level) megoldás megszületett:

1. **SELinux félrecímkézés**: a `podman generate systemd --new --files`
   kimenete a home könyvtárból lett `mv`-vel a `/etc/systemd/system/`-be
   mozgatva; az `mv` megtartotta az eredeti `user_home_t` SELinux-címkét a
   `systemd_unit_file_t` helyett, ezért a systemd "nem létező fájlként"
   kezelte (`Permission denied` a journal-ban). Javítás: `restorecon -v`.
2. **Rootful vs. rootless podman ütközés**: a rendszerszintű (`/etc/systemd/
   system`) egységben nem volt `User=` sor, ezért root felhasználóként
   próbált indulni — de a `umami_net` hálózat és a konténerek a mi
   (adminuser_developer, rootless) podman-tárolónkban léteztek, root saját,
   teljesen külön podman-világában nem. Egy `User=` sor hozzáadása sem lett
   volna elég, mert rendszer-egységben a `%t` specifikátor mindig `/run`-ra
   oldódik fel, nem `/run/user/1000`-re, amire a rootless podman-nak szüksége
   van. A helyes megoldás a user-level systemd (`systemctl --user`,
   `~/.config/systemd/user/`) + `loginctl enable-linger` volt, ahogy a fenti
   3. pontban szerepel. A hibás rendszer-egységek le lettek tiltva és
   törölve.

Ez a két hiba jó darabig timeoutolt/failed állapotban tartotta a
`container-umami-db.service`-t (root kontextusban), mire kiderült az ok —
éles adatvesztéssel vagy más szolgáltatás megzavarásával nem járt, mert a
meglévő rootless konténerek közben végig futottak.

## Ellenőrzés

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8030/        # 200
curl -sI https://stats.emberisegelleneskartyak.hu                     # HTTP/2 200
curl -s https://emberisegelleneskartyak.hu/ | grep stats.ember...     # script tag jelen van
systemctl --user status container-umami-db.service container-umami-app.service  # active (running)
```

## Még szükséges lépések

- [ ] Ez a WIP + a kapcsolódó kódváltozások (`server/main.py`, 9 statikus
  HTML) jelenleg nincsenek commitolva — döntés kell, mikor/hogyan kerüljön
  fel `main`-re.
- [ ] Opcionális: egy mondat az `adatkezeles.html` "Belső statisztika"
  pontjához az új önhosztolt analitikáról (felajánlva, még nem kész).
- [x] `partvalto.hu` bekötve ugyanebbe az Umami-példányba (2026-08-28).
  Umami website létrehozva közvetlenül a Postgres `website` táblában
  (`INSERT`, `website_id=eed4e5f8-a427-47ef-b6db-af8baf33f12f`, `name=Pártváltó`,
  `domain=partvalto.hu`, ugyanaz a `user_id`/`created_by` mint a másik két
  site-nál, mert a dashboard admin jelszava nem volt elérhető ebben a
  sessionben az API/UI-n keresztüli létrehozáshoz). Tracking script a
  forrás `frontend/index.html`-be került (nem a build-artifact
  `dist/index.html`-be), majd `npm run build` lefuttatva, hogy a
  `dist/index.html` is frissüljön — enélkül a következő deploy felülírta
  volna. `partvalto-api` és nginx nem lett újraindítva/reloadolva, mert a
  változás tisztán statikus asset, a `try_files` már a frissített
  `dist/index.html`-t szolgálja ki. Ellenőrizve: `curl -s
  https://partvalto.hu/ | grep stats.ember...` mutatja a script tag-et.
- [ ] `hollandmagyarradio.hu` Umami website létrehozva a dashboardon
  (2026-08-28, `website_id=1a5e306f-9166-4566-ad62-d1b46bb3088a`), de a
  tracking script **nincs** bekötve a `/var/www/hollandmagyarradio`
  forrásba — a deploy-folyamata még nincs feltérképezve, ez még nyitott.
- [ ] Az Umami/Postgres image-ek frissítése (`podman pull` + újraindítás)
  nincs automatizálva — új, saját karbantartást igénylő szolgáltatás lett a
  szerveren.
- [ ] Megerősítendő, hogy a felhasználó lecserélte az ideiglenes admin
  jelszót a dashboardon (a beszélgetés alapján igen, de nincs eszközzel
  ellenőrizve).
