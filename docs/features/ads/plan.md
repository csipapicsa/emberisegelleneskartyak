# Emberiség Ellenes Kártyák — Umami analitika és AdSense előkészítési terv

**Dátum:** 2026-10-16  
**Weboldal:** https://emberisegelleneskartyak.hu/  
**Cél:** pontosabb látogatottsági és játékmenet-analitika; a reklámozás lehetőségének felelős megvizsgálása; később egyetlen diszkrét banner megjelenítése kizárólag a főoldalon és – amennyiben megfelel a szabályoknak – a csatlakozási oldalon.

> **Claude / fejlesztői feladat:** Először auditáld a jelenlegi alkalmazást, a tényleges Umami-verziót és a Google szabályzatok alkalmazhatóságát. A számok képernyőképekből származnak, nem API-exportból. Ne tekintsd az itt szereplő állításokat már ellenőrzött kód- vagy konfigurációs tényeknek. Előbb adj rövid diagnózist és tételes implementációs tervet; csak utána módosítsd a kódot. **Valódi AdSense-kódot, CMP-t, automatikus hirdetést vagy hirdetéssel kapcsolatos hozzájáruláskezelést ne aktiválj a tulajdonos külön jóváhagyása nélkül.**

## 1. Kiinduló adatok (Umami, utolsó 90 nap)

| Mutató | Jelzett érték |
|---|---:|
| Visitors | 4 270 |
| Visits | 6 250 |
| Views | 39 600 |
| Bounce rate | 35% |
| Visit duration | 7 perc 23 másodperc |

**Fontos:** A `Visitors`, `Visits`, `Views`, `Entry page`, `Exit page` és `Sources` nem egymással felcserélhető mérőszámok. A 39 600 oldalmegtekintés **nem** 39 600 reklámmegjelenés. A 7m23s Umami-mutató **nem** bizonyítottan ennyi aktív játékidő: az Umami időtartam-számítása és az egyoldalas játékmenetek sajátosságai miatt külön mérés szükséges.

### Oldalak — Path, Visitors (nem Views)

A leglátogatottabb útvonalak:

| Path | Visitors |
|---|---:|
| `/` | 3 530 |
| `/join` | 2 260 |
| `/create` | 1 290 |
| `/kartyak` | 374 |
| `/kartyaotlet.html` | 149 |
| `/kartyaszavazas` | 54 |

A `/game/NNNN` és `/room/NNNN` útvonalak külön-külön jelennek meg, ezért a top 10 lista alulbecsüli e két **oldalcsoport** jelentőségét. A számok nem konverziós tölcsért jelentenek; ugyanaz a látogató több útvonalon is szerepelhet.

### Entry/Exit és URL-fülek — példák

- Entry path `/`: kb. **3 240 (81%)**; `/join`: **611 (15%)**; `/create`: **98 (2%)**.
- Exit path `/`: kb. **1 900 (70%)**; `/join`: **611 (23%)**; `/create`: **66 (2%)**.
- Az URL fülön `/` kb. **3 450 (48%)**, `/join` **1 690 (23%)**, `/create` **1 280 (18%)**; itt a query stringek eltérően csoportosulnak.
- Külön kártyás URL-ek is láthatók: pl. `/kartyak?edition=5`.

**Értelmezési figyelmeztetés:** Ezeknél a panelek eltérő nevezők és az Umami saját „Visitors” bontásai szerepelnek. A százalékokat tilos egyszerűen egy klasszikus felhasználói funnelként vagy a 39 600 megtekintés eloszlásaként kezelni.

### Forgalmi csatornák (a képernyőképen)

| Channel | Visitors | Jelzett arány |
|---|---:|---:|
| Organic search | 2 310 | 47% |
| Direct | 2 180 | 45% |
| Paid ads | 220 | 5% |
| Organic social | 131 | 3% |
| Referral | 18 | 0% |
| LLM | 3 | 0% |

A keresőkből (kiemelten Google), valamint a direct csatornából érdemi forgalom érkezik. A `Paid ads` itt **látogatásszerzési forrás**, **nem** bevétel. A Direct nem kizárólag kézzel beírt URL-t vagy bizonyítottan visszatérő látogatót jelent. Az LLM forgalom egy részének referrere hiányozhat.

## 2. Üzleti és UX-döntés: csak egy diszkrét hirdetés

**Tulajdonosi döntés:**

1. Hirdetés **csak** a főoldalon (`/`) és feltételesen a csatlakozási oldalon (`/join`) jöhet szóba.
2. **Nincs hirdetés** játék közben (`/game/*`), lobbyban (`/room/*`), kártyaböngészőben (`/kartyak*`), kártyajavaslatnál, szavazásnál, szobakészítésnél (`/create`) vagy más útvonalon.
3. Cél: legfeljebb **egy**, mobilra optimalizált, az oldal alsó részén elhelyezett banner az engedélyezett oldalakon; ne rontsa a felhasználói élményt.
4. Kezdetben **ne legyen AdSense Auto ads**, különösen ne automatikus overlay, anchor vagy vignette, mert azok olyan oldalon is megjelenhetnek, ahol nem szeretnénk. A betöltést és a hirdetéshelyet route-level whitelisttel kell korlátozni.
5. A játékszabályokat, a WebSocket-kommunikációt és a kártyatartalmat a monetizáció érdekében **ne alakítsuk át**.

**Fontos megfelelőségi különbség:** A kártyák hiánya egy adott oldalon csökkentheti a problémás tartalom közelében megjelenő hirdetések kockázatát, de **nem garantálja** az AdSense webhelyszintű jóváhagyását. Ráadásul a `/join` tisztán funkcionális (űrlap/navigáció) oldalként **alacsony értékű tartalom** miatt problémás lehet. Ha a felülvizsgálat alapján nem megfelelő, **csak a főoldalon** legyen reklám; ne töltsük fel mesterséges SEO-szöveggel pusztán a hirdetés miatt.

## 3. Elsődleges fejlesztés: az Umami jelenlegi állapotának auditja

A tényleges implementációt kell feltérképezni:

- Pontosan melyik Umami-verzió fut? Cloud vagy self-hosted? Milyen tracking script/verzió?
- Mely HTML-oldalak töltik be a trackert? Van-e duplikált script vagy dupla pageview? Működik-e mobilon, privát böngészésben, adblocker mellett, újratöltéskor?
- Milyen query stringeket és room code-okat gyűjt most? Vannak-e technikailag felesleges paraméterek, személyes adatok vagy véletlenszerű szobakódok?
- A váratlan vagy nagyon magas Views/Visits arány oka oldalletöltésekben, route-váltásokban, eseményekben vagy mérési hibában keresendő? Vizsgálni, nem találgatni.
- Hány **Views** jut valójában a `/`, `/join`, `/create`, `/room/*`, `/game/*`, `/kartyak*` csoportokra? Export/API vagy hiteles aggregáció alapján.
- Egyezik-e a riport dátumtartománya és időzónája az összes képernyőképen?
- A nyitóoldal és a `/join` tényleges, renderelt HTML/DOM tartalmaz-e véletlenszerű kártyákat, explicit szöveget, chatet vagy más, a reklámmegfelelőségre ható tartalmat? (A főoldali kártyapéldák lehetőségét külön ellenőrizni.)

**Elvárt első deliverable:** audit-összefoglaló `current state → problem → proposed fix → risk → priority`, módosítás nélkül.

## 4. Oldalútvonalak normalizálása és megbízható Views-riport

Javasolt **analitikai kategóriák** (a valódi route-okat érintetlenül hagyva):

| Valódi URL példa | Analitikai csoport |
|---|---|
| `/` | `/` |
| `/join` | `/join` |
| `/create` | `/create` |
| `/room/6454` | `/room/:code` |
| `/game/6920` | `/game/:code` |
| `/kartyak?edition=5` | `/kartyak` (edition eseményparaméterként, ha hasznos) |
| `/kartyaotlet.html` | `/kartyaotlet.html` |
| `/kartyaszavazas` | `/kartyaszavazas` |

**Megvalósítási lehetőségek a tényleges Umami-verzióhoz igazítva:**

- Elsőként ellenőrizni, hogy az Umami **Breakdown / filter / API** már képes-e helyes csoportosított exportot adni a régi adatokra. A múltbeli adatokat lehetőleg ne módosítsuk.
- Az új pageview-khoz szükség esetén a tracker `data-before-send` hookjával **csak az analitikai payload URL mezőjét** normalizálni; semmiképpen sem módosítani a böngésző valódi URL-jét, az alkalmazás routingját, a meghívólinkeket vagy a szobakódokat.
- Alternatíva: verziótól függően `data-auto-pageview="false"` és az Umami dokumentált `umami.track(props => ({...props, url: normalizedUrl}))` megoldása. Ügyelni arra, hogy **pontosan egy** pageview keletkezzen valódi lapbetöltésenként/valódi route-váltásonként, ne legyen duplikáció.
- A `data-auto-track="false"` **nem** automatikusan felcserélhető a `data-auto-pageview="false"` opcióval; az utóbbi csak megfelelő Umami-verzióban elérhető.
- Szobakód, session UUID, név, szabad szöveg, kártyaszöveg ne kerüljön tracking URL-be vagy event payloadba. A címkék legyenek alacsony kardinalitásúak.
- Egyéb URL-ekhez fallback; ne legyen csendes adatvesztés.

**Acceptance tesztek:** 10 külön `/game/NNNN` és 10 külön `/room/NNNN` látogatás analitikában egy-egy összesített oldalcsoportra essen, de mindegyik valós meghívólink és játékszoba működjön. Nincs duplikált pageview és nincs szivárgó room code.

## 5. Eseményalapú analitika — mire használják ténylegesen a játékot?

Az eseményeket a **már meglévő, sikeres szerver-visszajelzésekhez vagy állapotváltásokhoz** kösd, ne pusztán gombnyomáshoz. A WebSocket protokollt és a játékmenetet ne módosítsuk csak a mérés kedvéért.

### Ajánlott kezdeti event-séma

| Event | Mikor egyszer? | Hasznos, nem érzékeny property |
|---|---|---|
| `room_created` | Sikeres szobalétrehozás | `source_page` |
| `room_joined` | Ténylegesen sikeres belépés | `entry_method` (pl. code/link, ha megbízhatóan ismert) |
| `game_started` | Játék kezdetének visszaigazolt állapotváltásakor | `player_count`, `voting_mode`, `planned_rounds` |
| `round_completed` | Befejezett körre érkezett állapotnál | `round_number`, `player_count` |
| `game_finished` | Végjáték szerveroldali eseménynél | `completed_rounds`, `player_count` |
| `game_abandoned` | **Csak ha megbízhatóan meghatározható** | lehetőleg aggregált ok |
| `cards_browse_opened` | Kártyaböngésző megnyitásakor | `edition_id` (publikus katalógus-ID) |

**Duplikációs probléma:** Ha minden játékos kliensoldala küld egy `game_started` eseményt, az **játékosonkénti észlelések számát** jelenti, nem egyedi játékokét. Két eltérő KPI szükséges:

1. **Player participation**: hány játékos csatlakozott / vett részt, kliensoldali Umami-eventekből is becsülhető.
2. **Unique games**: hány szoba indított ténylegesen játékot, lehetőség szerint adatvédelmi szempontból minimális, aggregált szerveroldali számlálóból, vagy egyetlen kijelölt eseménykibocsátóból, megfelelő reconnect/idempotencia kezeléssel. **Ne** küldjük a szobakódot vagy session ID-t az Umamiba csak a deduplikáció miatt.

A `round_completed` és `game_finished` eseményeknél reconnect, dupla `room_state`/`round_end` üzenet és oldalak közötti átmenet ne okozzon véletlen többszörös számlálást. Ha a pontos egyedi játékszámhoz külön perzisztens aggregált számláló kell, előbb javasolj minimális megoldást és kérj jóváhagyást; a jelenlegi in-memory state-et ne alakítsd át indokolatlanul.

### Hasznos KPI-k

- Pageviews útvonalcsoportonként (`/`, `/join`, `/create`, `/room/*`, `/game/*`, `/kartyak*`).
- Kezdőlapi látogatók aránya, akik **legalább egyszer** megkezdik a csatlakozást vagy szobalétrehozást (jól definiált mérés és nevező).
- Sikeres room join / room create arány, ahol rendelkezésre áll megbízható számláló.
- Megkezdett játékok; résztvevők száma/játék; befejezett körök; befejezett játékok aránya.
- Ország, eszköztípus, forgalmi csatorna szerinti megoszlás (agregáltan).
- Visszatérés/retention, ha az Umami-verzió és adatvédelmi beállítások ezt megbízhatóan mérik.

### Valós aktivitási idő

Ne nevezd a jelenlegi Umami `Visit duration` értékét „átlagos játékidőnek”. A websockets játék akár hosszú ideig ugyanazon a lapon marad. Ha külön aktivitásmérés szükséges, kliensoldali `visibilitychange`/fókusz és *valódi játék-interakciók* alapján csak **összesített, kerekített időtartam-sávokat** továbbíts (pl. 0–5, 5–15, 15–30, 30+ perc), és ne másodpercenkénti heartbeatet. Előbb tisztázd a KPI definícióját, megbízhatóságát és privacy hatását.

## 6. AdSense-kísérlet: előbb megfelelőség, aztán reklámkód

### Reklámhelyek whitelistje

**Tervezett engedélyezett útvonalak:** `/`; `/join` **csak ha megfelel a content-value policynek**. Semmilyen egyéb route-on nem töltünk be hirdetési egységet.

### Kötelező megfelelőségi audit

1. **Webhely- és oldalszintű tartalom:** A Google tiltja a szexuálisan explicit tartalmú oldalak monetizálását, és korlátozhatja a szexuális/sokkoló/trágár tartalom melletti hirdetéseket. A kártyajáték általános témája és az oldalakon ténylegesen megjelenő tartalom is releváns lehet; a reklámmentes játéklapok **nem** jelentenek automatikus jóváhagyást.
2. **`/join` alacsony tartalmi érték:** A pusztán navigációt, bejelentkezést, kódbevitelt szolgáló oldalak Google-hirdetésre alkalmatlanok lehetnek. Ha problémás, hagyd ki a `/join`-t, és erről világosan tájékoztasd a tulajdonost.
3. **EEA/UK/Svájc hozzájárulás:** Google által tanúsított, IAB TCF-integrált CMP szükséges a Google vonatkozó hirdetési feltételei szerint. Vizsgáld a Google Privacy & messaging CMP megfelelő beállítását, a hozzájárulás előtti script/betöltés működését és a privacy policy frissítését. A „csak megjelenítésalapú” reklám **nem** mentesít önmagában a privacy kötelezettségek alól.
4. **Policy és UX:** ne ösztönözzön véletlen kattintásra, ne fedjen navigációt vagy csatlakozási gombokat, ne utánozzon rendszerüzenetet, ne legyen megtévesztő. Mobil safe area, képernyőmagasság, billentyűzet megnyitása és layout shift ellenőrzendő.
5. **Első körben statikus placeholder** (belső tesztre) az engedélyezett oldalon; valódi reklám csak kifejezett engedéllyel és AdSense jóváhagyás után.

**Banner-design:** egyetlen nem tolakodó, reszponzív hirdetéshely az oldal alsó részében / footer fölött. Ne legyen automatikusan sticky vagy teljes képernyőt lefedő. Ha a bannerhez nem görgetnek le, várhatóan kevesebb hirdetés lesz látható; ezt UX-kompromisszumként fogadjuk el, ne próbáljuk kényszerített scrollal vagy mesterséges frissítéssel növelni a számokat.

### Megjelenítések és bevétel mérése

- Az AdSense elsősorban **hirdetésmegjelenítések** után fizet; kattintás nélkül is keletkezhet bevétel, de nincs fix összeg minden megjelenésért.
- **Umami:** becsülhető reklámra elvileg alkalmas oldalmegtekintés és látogatói megoszlás.
- **AdSense:** az igazolt `ad impressions`, coverage/fill, actual revenue, Page RPM / Impression RPM, policy restriction a hiteles bevételi forrás.
- Az Umami oldalletöltést **tilos** AdSense-impressionnek vagy bevételnek feltüntetni. Ne készíts mesterséges ad-refresh mechanizmust.

## 7. Bevételi becslés — forgatókönyv, nem ígéret

Korábbi teljes oldalas, pusztán illusztratív Page RPM-számítás a **39 600 Views** alapján:

| Feltételezett Page RPM | 90 nap, ha minden view monetizálható lenne |
|---|---:|
| 0,50 EUR | 19,80 EUR |
| 1,00 EUR | 39,60 EUR |
| 2,00 EUR | 79,20 EUR |
| 5,00 EUR | 198,00 EUR |

Ez **nem** a valós oldalra adott bevételi előrejelzés: egyetlen banner lenne, csak a `/` és esetleg `/join` útvonalon; a kártyajáték témája és a hozzájárulás/adblocker/policy-korlátozások mind csökkenthetik a tényleges monetizálható forgalmat. Korábban 5–40 EUR / 90 nap nagyságrendi tervezési lehetőségként merült fel, de nincs elég információ az alátámasztásához. **Következő lépés:** először a `/` és `/join` **Views** pontos megmérése, ország és eszköz szerinti bontása; majd sikeres jóváhagyás után AdSense adatokból saját RPM becslése.

Számítás, **csak azonos definíció mellett**: `eligible_pageviews / 1000 × realized_page_RPM`. Ha a Google-ben már szerepel az oldal szintű Page RPM, ne szorozd még egyszer a hirdetések számával vagy az impression rate-tel. Az impression RPM eltérő nevezőjű mutató.

## 8. Implementációs sorrend és elfogadási kritériumok

### P0 — Felmérés (először csak olvasás)

- [ ] Git státusz / aktuális fájlok / rendszer- és Umami-verziók vizsgálata.
- [ ] Tracker, route-ok, analytics eventek, oldalak valós renderelt tartalmának auditja.
- [ ] Azonos időszakra Views export oldalútvonal-csoportonként; korábbi adatok mentése.
- [ ] AdSense policy check `/` és `/join` oldalakra, kifejezetten low-value és explicit content szempontból.
- [ ] Rövid auditjelentés készítése, kockázatokkal.

### P1 — Umami mérés helyreállítása

- [ ] Route-ok analitikai normalizálása új adatoknál / megfontolt aggregálás a régieknél.
- [ ] Egyetlen pageview/valós oldalbetöltés; nincs duplikáció.
- [ ] Játékesemények minimális, privacy-safe bevezetése; pontos event definíciók.
- [ ] Ellenőrzött dashboard / export: Views, Visitors és események külön, helyes nevezőkkel.
- [ ] Reconnect, mobil, több játékos, újratöltés, URL query és cookie/consent teszt.

### P2 — Reklám előkészítése (aktiválás nélkül)

- [ ] Hirdetési route whitelist, alapértelmezetten OFF.
- [ ] Mobilra optimalizált, egyetlen placeholder reklámhely; játékoldalon soha.
- [ ] Google CMP / adatkezelési tájékoztató / script-consent sorrend tervének áttekintése.
- [ ] AdSense regisztráció és review a tulajdonos döntése alapján.

### P3 — Külön jóváhagyás után

- [ ] AdSense valódi hirdetési egység + megfelelő CMP integrálása kizárólag engedélyezett oldalon.
- [ ] Teszt: semmi ad-network request `/game/*`, `/room/*`, `/kartyak*`, `/create` útvonalon.
- [ ] 2–4 hét valós AdSense adatok követése: impressions, revenue, policy status, UX regresszió, oldalsebesség.
- [ ] Ha `/join` nem megfelelő, reklam csak `/` oldalon.

## 9. Üzemeltetési biztonság — kötelező keretek Claude számára

Projekt: `/opt/emberisegellenes_kartyak/`; AlmaLinux 9; nginx + FastAPI/uvicorn + natív WebSocket; vanilla HTML/CSS/JS; SQLite csak kártyákhoz és feedbackhez; minden aktív játék **memóriában van**.

- **Olvass módosítás előtt.** Ne találj ki fájlokat és ne feltételezd, hogy pontosan a régi struktúra fut. Nézd meg a jelenlegi kódot és konfigurációt.
- **Ne refaktoráld a játékmenetet.** Az analitika kizárólag kiegészítő, hibás tracker soha ne akadályozza a room create/join/game flow-t.
- **Ne küldj PII-t vagy identitást Umamiba:** session UUID, username, chat/feedback, kártyaszöveg, szobakód, raw WebSocket message kizárva. Ne építs rejtett felhasználókövetést.
- **Nincs külön staging.** Először biztonságos, visszaállítható és a meglévő felhasználókat nem érintő változtatások; diff és snapshot/backup. Kockázatos szerver- vagy DB-módosításnál mentés.
- **Szerver restart = aktív játékok elvesztése.** Ne indítsd újra automatikusan a szolgáltatást csúcsidőben vagy kifejezett jóváhagyás nélkül. Frontend-módosítás után hard refresh/cache-verziózás; minden módosítás után működési és logellenőrzés.
- A hirdetés/CMP új külső függőség; network failure/adblocker esetén az oldalnak és játéknak továbbra is működnie kell.
- Adj implementáció utáni rövid jegyzőkönyvet: módosított fájlok, tesztek, eredmények, esetleges nyitott kérdések, rollback.

## 10. Claude-nak szóló konkrét megbízás

> Vizsgáld át az Emberiség Ellenes Kártyák jelenlegi forráskódját és Umami-analitikáját a fenti terv alapján. Először **csak olvasás/audit**, majd készíts priorizált, minimális beavatkozású fejlesztési tervet. Az első implementáció célja a helyes Views-riportok, route-normalizálás és privacy-safe game eventek bevezetése a meglévő multiplayer működés megőrzésével. A reklámok tekintetében kizárólag a főoldal (`/`) és – ha tartalmilag megfelel – a csatlakozási oldal (`/join`) jöhet szóba, egyetlen alsó bannerrel. **Semmilyen ad unit nem kerülhet játékra, lobbyba, kártyalistára vagy más oldalra.** AdSense-kód és CMP élesítését külön hagyjuk jóvá. Előbb mutasd meg a megállapításokat, a konkrét fájlokat, a mérési definíciókat, a teszttervet és a lehetséges kockázatokat; a jóváhagyott módosításokat ellenőrizd tényleges adatokkal és alkalmazás-tesztekkel.

---

## Hivatalos dokumentáció és szabályzat (ellenőrizve: 2026-10-16)

- Umami tracker configuration: https://docs.umami.is/docs/tracker-configuration
- Umami tracker functions / pageviews: https://docs.umami.is/docs/tracker-functions
- Umami custom events: https://docs.umami.is/docs/track-events
- Umami metric definitions: https://docs.umami.is/docs/metric-definitions
- Umami Breakdown insight: https://docs.umami.is/docs/breakdown
- Google Publisher Policy — no/low-value content: https://support.google.com/publisherpolicies/answer/11112688?hl=en
- Google Publisher Policy — sexually explicit content: https://support.google.com/publisherpolicies/answer/10438119?hl=en
- Google Publisher Restrictions — sexual content: https://support.google.com/publisherpolicies/answer/10400657?hl=en
- Google Publisher Restrictions — shocking/profane content: https://support.google.com/publisherpolicies/answer/10437538?hl=en
- AdSense CMP rules for EEA/UK/CH: https://support.google.com/adsense/answer/13554116?hl=en
- Google AdSense impression-based payment FAQs: https://support.google.com/adsense/answer/14129646?hl=en

*Ez technikai/üzleti előkészítési terv, nem jogi tanács és nem Google-jóváhagyási garancia.*
