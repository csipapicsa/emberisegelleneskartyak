# Adatvédelmi implementációs terv

## Cél

Az oldal működéséhez szükséges adatkezelés megtartása, a statisztikához és a
kártyaötlet-szavazáshoz kapcsolódó IP-adatkezelés minimalizálása, valamint egy
valósághű, könnyen elérhető adatkezelési tájékoztató kiadása.

## Megvalósítási sorrend

1. **IP-minimalizálás és adatmegőrzés** — elkészült ezen az ágon.
   - A kártyaötlet-szavazás a nyers IP helyett célhoz kötött HMAC-azonosítót tárol.
   - Az analitika nem tárol játékosnevet, szobakódot vagy user-agentet; az
     egyedi látogatói méréshez célhoz kötött HMAC-azonosítót használ.
   - Az analitikai események megőrzési ideje 90 nap; induláskor a régi,
     szükségtelen azonosító mezők törlődnek.
2. **Üzemeltetői konfiguráció** — telepítés előtt kötelező.
   - A szerveren jöjjön létre a `/etc/emberisegellenes/privacy.env` fájl a
     `privacy.env.example` alapján, erős, titkos
     `EEK_PRIVACY_HMAC_SECRET` értékkel.
   - Ezt a titkot nem szabad commitolni, e-mailben küldeni vagy a nyilvános
     tájékoztatóba írni.
   - A meglévő nyers-IP-s szavazatok indításkor ebből képzett HMAC-ra
     migrálódnak. Titok nélkül a szolgáltatás szándékosan nem indul el, így
     nem marad észrevétlenül nyers IP-t tároló régi séma.
3. **Nyilvános adatkezelési tájékoztató** — a következő implementációs lépés.
   - Külön `/adatkezeles` oldal és lábléc-link minden nyilvános oldalon.
   - A szövegben külön cél/jogalap/megőrzés a játékmenetre, chatre,
     kapcsolatfelvételre, kártyaötletre, szavazásra és belső statisztikára.
   - A `localStorage`-t (session, név és beállítások) külön, közérthetően fel
     kell tüntetni. A játékmenethez szükséges session külön hozzájárulás
     nélkül használható; a kényelmi beállításokat felül kell vizsgálni.
4. **Dokumentálás és üzemeltetés**.
   - Egyszerű belső adatkezelési nyilvántartás és a tárhelyszolgáltatóval
     adatfeldolgozói szerződés.
   - Nginx hozzáférési/hibanaplók tényleges megőrzési idejének rögzítése.
   - Hozzáférési jogosultságok, mentések és törlési folyamat ellenőrzése.

## A tájékoztató kiadásához még szükséges döntések

- az adatkezelő teljes neve és kapcsolati e-mail-címe;
- tárhelyszolgáltató, mentési szolgáltató és — ha használatban van —
  TURN/hangszolgáltató neve és adatfeldolgozói szerepe;
- a kapcsolatfelvételi üzenetek és kártyaötletek pontos megőrzési ideje;
- az nginx access/error logok tényleges megőrzési ideje;
- van-e kiskorúaknak szóló használat vagy harmadik országbeli szolgáltató.

## Kiadási ellenőrzőlista

- [ ] A titkos HMAC-konfiguráció létrehozva a szerveren.
- [ ] A systemd unit és nginx konfiguráció ellenőrizve, majd újratöltve.
- [ ] Adatkezelési tájékoztatóban minden fenti döntés kitöltve.
- [ ] A tájékoztató linkje elérhető a főoldalról, kapcsolatfelvételnél és
  kártyaötlet/szavazás oldalról.
- [ ] Adatfeldolgozói szerződés és belső nyilvántartás elkészült.
