AdSense — teendők (2026-10-16)
Részletek: docs/features/ads/privacy-notice-text.md, docs/features/ads/plan.md

Gergő
[x] Auto ads kikapcsolva
[x] Auto optimize kikapcsolva
[x] GDPR-üzenet (CMP) közzétéve: magyar, Hozzájárulás / Nem járulok hozzá (minden ország) / Beállítások,
    Optimize off, logó: static/logo-wide.png
[x] Szerver-újraindítás → /ads.txt él (2026-10-16 23:13)
[ ] Várni a Google jóváhagyására (Request review elküldve 2026-10-16)
    → ha megjött, szólni Claude-nak; ha elutasítás, az indoklást elhozni

Claude (a jóváhagyás után)
[ ] AdSense-szkript betöltése csak /, /join, /create oldalon (static/js/ads.js), ADS_ENABLED = true
[ ] also-banner (320×50, slot 1343136630) az adSlot dobozba; asztalin is 320×50, középen;
    320 px széles telefonon ne lógjon ki
[ ] Hirdetési szakasz az adatkezeles.html-be + „Hatályos” dátum
[ ] Hozzájárulás visszavonása: a Google CMP „Adatvédelmi beállítások” linkje megjelenik-e
    a 3 oldalon; ha nem, saját link a láblécbe (googlefc.showRevocationMessage())
[ ] tests/test_ad_banner.py frissítése: Google-kód csak a 3 engedélyezett oldalon
[ ] Élesítés után: ad-network kérés nincs /game, /room, /kartyak oldalon (böngészőben ellenőrizni)

Hangfunkció használatának mérése (következő alkalom, Claude)
Kérdés: hallják-e ténylegesen egymást a játékosok, nem a gombnyomások és nem a saját mikrofon.
[ ] Kliens (static/js/voice.js): csak a FOGADÓ oldal — getStats() inbound-rtp audioLevel /
    totalAudioEnergy ~10 mp-enként; „hallott hangot” = hallható bejövő hang ÉS a lap előtérben
    (document.visibilityState). A saját mikrofont NEM mérjük. Nincs hangtartalom,
    nincs megnevezve, ki beszélt.
[ ] Játék végén / kilépéskor egyetlen összesítő üzenet a szervernek:
    hallott másokat: 0 / <1 / 1–5 / 5–15 / 15+ perc
[ ] Szerver: log_event("voice_heard", sáv) az analytics.db-be (90 nap), név és szobakód nélkül
[ ] /admin/stats: „a játékok X%-ában hallották egymást”, átlagos hallgatási idő
[ ] adatkezeles.html „Belső statisztika”: „a hangfunkciónál összesítve, hogy érkezett-e hallható
    hang (tartalom nélkül)”
[ ] Tesztek: pytest (szerver) + node (voice.js), mutation-check
[ ] Szerver-újraindítás kell
Megjegyzés: a TURN-kapcsolatok száma (~1060 / 30 nap) NEM mutat használatot, mert a hang
mindenkinél automatikusan csatlakozik.

Kész
[x] Hirdetési hely (/, /join, /create), alapból kikapcsolva, ?ads=preview előnézet
[x] Umami az adatkezelési tájékoztatóban, 2 éves megőrzés + napi törlő időzítő
[x] AdSense tulajdonjog igazolva meta taggel, /ads.txt route
[x] also-banner hirdetési egység létrehozva
[x] Umami útvonal-normalizálás: /room/:code, /game/:code, /join?code=xxxx (élesben ellenőrizve 2026-10-16)

Mentések felülvizsgálata (2026-11-06)
A napi mentés: emberisegellenes-db-backup.timer, 03:05 → scripts/backup_databases.py, 30 nap megőrzés.
Minden *.db-t ment az adatmappa gyökerében és a database/ alatt. backups/ most 181 MB.
[ ] Nem használt DB-k: cards.db, cards_safety_2026_07_11.db, cards_safety_2026_07_27.db
    (card_master.db váltotta őket, a kód nem olvassa). Napi 30 példány, ~52 MB.
    Döntés: egyszer archiválni, utána törölni, és kivenni a mentésből.
[ ] Glob helyett kifejezett lista: melyik DB-t mentjük és miért.
[ ] card_master.db csak feltöltéskor változik: 12 példány (65 MB), ebből 4 különböző.
    Csak akkor menteni, ha változott (hash).
[ ] A konténeres átállás (Phase 2.4) után az adat /srv/eek/prod alatt lesz:
    a mentés EEK_DATA_DIR-rel fusson oda, és legyen visszaállítási próba.
[ ] Offsite másolat: most minden mentés ugyanazon a szerveren van.

Konténer / staging — teendők (2026-11-06)
[x] Staging él: staging.emberisegelleneskartyak.hu, a :main image-et követi (podman auto-update, 5 perc)
[x] Függőségek zárolása: requirements.in → requirements.txt (pip-compile, hash-ekkel), Dependabot.
    Élesben starlette 1.0.0 futott, 5 ismert sérülékenységgel; a CI audit nem látta, mert nem a
    telepített verziókat nézte.
[ ] WebSocket: ha a böngészők egyszerre zárnak (e2e teszt vége), a websocket_endpoint ciklusa egy már
    lezárt socketen hívja a receive-et → "Need to call accept first" traceback. A játékot nem rontja el.
    Elkapni és tesztet írni rá.
[x] Szavazós mód: ha lejár az idő és senki nem szavazott, a tally_votes kihagyja a kézfeltöltést,
    így a következő kör 9 lappal indul (a rákövetkező kör helyrehozza).
[ ] Staging: a Umami-szkript a staging forgalmát is az éles statisztikába számolja.

Következő lépések, sorrendben (2026-11-06, a projekt-review alapján)
[ ] 1. Éles átköltöztetése konténerbe (Phase 2.4): eek user, /srv/eek/prod, :prod tag.
       BIZTONSÁG: ez zárja le a P7-et (a játék most wheel tag userként fut → kódfuttatási hiba = root).
       RENDBETÉTEL: minden adat /srv/eek/prod alá kerül, a kódmappában nem marad adatbázis.
       A régi service maradjon visszaállításnak; a backup és cleanup timer EEK_DATA_DIR-rel menjen.
    [ ] Promote workflow (:main → :prod) és docs/runbooks/deploy.md: merge → staging → éles, visszaállítás.
    [ ] Visszaállítási gyakorlat: előző image élesbe, majd vissza.
    [ ] Mentés-visszaállítási próba: mentésből ideiglenes mappába, PRAGMA integrity_check,
        az app elindul a visszaállított adattal.
    [x] Lemez: 93% → 84% (/opt/local_llm modelljei törölve). Még szabadítható: pip-cache-ek, régi image-ek (~5 GB).
    [ ] CI deploy-ellenőrzés: merge után megvárni, hogy a staging /healthz az új commitot mutassa
        (GitHub environment "staging").
[ ] 2. ML-életciklus a gépi játékossal: docs/ml/roadmap.md, lépésenként: docs/ml/implementation-plan.md (GCP-n tanítás, saját MLflow szerver,
       kiszolgálás a saját szerveren). Mérföldkövek: MLflow szerver → helyi pipeline → ugyanaz Cloud Run
       Jobként → model service + bot + promóció/visszaállítás → monitorozás.
[ ] 3. Konténer-image sérülékenységvizsgálat (Trivy), előbb csak riport. A Dependabot PR-okkal együtt.
[ ] 4. Metrikák: kérésszám, 5xx arány, p95 válaszidő, aktív WebSocket-kapcsolatok → Prometheus →
       Grafana, egy riasztással. Az MLflow után, nem vele egy időben.
[ ] 5. Kis WebSocket terheléses teszt (10 → 25 → 50 kliens): hol az első szűk keresztmetszet.
[ ] README: "Engineering decisions" szakasz (miért SQLite, miért egy processzben a szobák, mik a
    korlátok, mit véd a CI és mit nem) + architektúraábra.

Projekt-higiénia (2026-11-06)
[ ] docs/ rendezése:
    - analitika.md törlése: 1525 sor Python-kód .md néven.
    - Elavult: PROJECT_STRUCTURE.md, PROGRESS.md (júniusi állapot) → frissített docs/architecture.md,
      a történet a worklogs/ alá.
    - Lezárt tervek és egyszeri jegyzetek (BUGFIX_timer_freeze.md, *_PLAN.md, adsense terv) →
      worklogs/ vagy docs/private/.
    - Témák szerinti mappák (operations/, features/, ml/), egységes kisbetűs fájlnevek, docs/README.md
      tartalomjegyzékkel. Az áttekintő doksik angolul (portfólió), a játékszövegek maradnak magyarul.
[ ] Élő mappa (/opt): cards.db, cards_safety_*.db, duplicate_cards.csv archiválása és törlése, az üres
    cron/ törlése, backups/ a kódmappán kívülre. A 2.4-es átköltöztetéssel együtt.
[ ] Megfontolni: a teendők GitHub Issues-ba költöztetése (a repóban látható, iparági gyakorlat).
