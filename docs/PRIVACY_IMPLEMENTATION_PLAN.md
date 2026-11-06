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
     `deploy/privacy.env.example` alapján, erős, titkos
     `EEK_PRIVACY_HMAC_SECRET` értékkel.
   - Ezt a titkot nem szabad commitolni, e-mailben küldeni vagy a nyilvános
     tájékoztatóba írni.
   - A meglévő nyers-IP-s szavazatok indításkor ebből képzett HMAC-ra
     migrálódnak. Titok nélkül a szolgáltatás szándékosan nem indul el, így
     nem marad észrevétlenül nyers IP-t tároló régi séma.
3. **Nyilvános adatkezelési tájékoztató** — folyamatban.
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

## Eldöntött alapértékek

- Adatkezelői kapcsolat: `gergo.gyori.project@gmail.com`.
- Tárhelyszolgáltató/adatfeldolgozó: Contabo.
- Kapcsolatfelvételi üzenetek: 12 hónap.
- Nyilvános üzenőfal: 30 nap; szobán belüli chat: a szoba megszűnéséig.
- Kártyaötletek: a játékkészlet tartalmaként korlátlan ideig megmaradnak;
  a beküldő személyéhez nem kapcsoljuk őket.
- Kártyaötlet-szavazati HMAC: 5 év.
- Nginx access- és error-logok: 14 nap; a hozzá tartozó logrotate-minta
  `deploy/logrotate/emberisegellenes` néven szerepel a projektben.

## Kiadási ellenőrzőlista

- [ ] A titkos HMAC-konfiguráció létrehozva a szerveren.
- [ ] A `deploy/systemd/emberisegellenes.service.d/privacy.conf` drop-in bemásolva
  `/etc/systemd/system/emberisegellenes.service.d/privacy.conf` alá; ez nem
  írja felül a meglévő TURN_SECRET-et.
- [ ] A systemd és nginx konfiguráció ellenőrizve, majd újratöltve.
- [ ] A logrotate-minta bemásolva `/etc/logrotate.d/emberisegellenes` alá.
- [ ] Adatkezelési tájékoztatóban minden fenti döntés kitöltve.
- [ ] A tájékoztató linkje elérhető a főoldalról, kapcsolatfelvételnél és
  kártyaötlet/szavazás oldalról.
- [ ] Adatfeldolgozói szerződés és belső nyilvántartás elkészült.
