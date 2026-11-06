AdSense — teendők (2026-10-16)
Részletek: docs/adatkezeles_hirdetes_szoveg.md, docs/emberisegellenes_umami_adsense_fejlesztesi_terv.md

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
