# Adatkezelési tájékoztató — hirdetési szakasz (élesítéskor kerül be)

Csak akkor kerüljön a `static/adatkezeles.html`-be, amikor a Google CMP be van
kapcsolva és az AdSense-kód a bannerhelyen van (`static/js/ads.js`). Előtte nem
igaz, ezért nem szerepelhet a tájékoztatóban.

## Törlendő mondatok

- „Nem használunk reklámcélú vagy külső analitikai követőkódot.” (Jogalap szakasz)
- „Harmadik fél reklám- vagy analitikai szolgáltatónak nem adunk át adatot.” (Címzettek szakasz)

## Módosítandó

„A böngészőben nem használunk klasszikus sütiket.” →
„Saját célra nem használunk sütiket. Ha a hirdetésekhez hozzájárulsz, a Google
hirdetési sütiket és hasonló technológiákat helyezhet el.”

## Új lista-pont

> **Hirdetések (Google AdSense):** a főoldalon, a csatlakozási és a szobalétrehozási
> oldalon egy hirdetési sáv jelenik meg, játék közben soha. A hirdetéseket a Google
> Ireland Ltd. szolgáltatja, amely a hozzájárulásod alapján sütiket és hasonló
> technológiákat használhat (személyre szabás, gyakoriság-korlátozás,
> csalásmegelőzés). Az adatok az EU–USA adatvédelmi keretrendszer alapján az USA-ba
> is továbbíthatók. Jogalap: hozzájárulás, amelyet bármikor visszavonhatsz az oldal
> alján lévő „Adatvédelmi beállítások” linkkel. Részletek:
> <https://policies.google.com/technologies/partner-sites>

## Élesítési ellenőrzőlista

1. AdSense → Adatvédelem és üzenetkezelés: Google CMP (EU/EGT, UK, CH) bekapcsolva.
2. AdSense → Automatikus hirdetések: **kikapcsolva**.
3. Fix méretű hirdetési egység a `adSlot` dobozba — létrehozva (2026-10-16):
   `also-banner`, 320×50, `data-ad-client="ca-pub-3693585614019775"`,
   `data-ad-slot="1343136630"`. Asztali gépen is 320×50, középre igazítva
   (a `.ad-slot-box` 468×60-as asztali méretét ehhez kell igazítani).
4. `ads.txt` a gyökérben a publisher ID-val.
5. A fenti szöveg a tájékoztatóba, „Hatályos” dátum frissítése.
6. `tests/test_ad_banner.py` frissítése (jelenleg tiltja az AdSense-kódot).

*Technikai javaslat, nem jogi tanácsadás.*
