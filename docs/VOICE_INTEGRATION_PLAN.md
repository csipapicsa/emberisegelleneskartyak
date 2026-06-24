# Hang integráció — tervezési dokumentum

## Összefoglalás

WebRTC P2P mesh alapú, böngészőközi hang, ahol a **szerver nem dekódol, nem mixel, nem dolgoz fel hangot** — csak a kapcsolatfelépítéshez szükséges üzeneteket továbbítja (signaling), és TURN proxyt biztosít NAT mögötti kliensek számára.

---

## Architektúra

```
[Player A] ──── Opus audio (WebRTC P2P) ────── [Player B]
     │                                               │
     │          coturn TURN (ha szükséges)           │
     └──────────────────────────────────────────────┘

[FastAPI WS] = csak signaling (offer / answer / ICE candidate)
             = meglévő /ws/<code> csatornán belül
```

### Miért P2P mesh és nem SFU?

- Max 5 fő/szoba → 10 peer kapcsolat összesen → triviális terhelés kliensen is
- Szerver **egyáltalán nem látja az audiót** coturn TURN esetén sem (csak relay, nem decrypt)
- Nincs külön Janus / Mediasoup daemon — semmi extra telepítés a meglévő Python service mellé
- coturn maga elenyésző CPU-t használ (csak UDP csomagokat proxyzik)

---

## Biztonsági modell

### 1. TURN időkorlátos credential (legfontosabb)

coturn **HMAC temporal credential** mechanizmust használunk:
- A szerver generál egy rövid életű (pl. 24 órás) username/password párt HMAC-SHA1 alapon
- A kliens csak ezt a credential-t kapja meg, a shared secret **soha nem megy ki**
- Lejárt credential-lel a coturn visszautasítja a TURN allokációt
- Nincs állandó jelszó, amit kiszivárogtatva visszaélhetnek

```
username = timestamp:session_id   (pl. "1745000000:abc123")
password = HMAC-SHA1(secret, username)
```

Endpoint: `GET /api/voice/credentials` — csak érvényes session_id-vel hívható

### 2. Signaling szoba-izoláció

- A signaling üzenetek (offer, answer, ice-candidate) a meglévő WebSocket rendszeren mennek át
- A szerver ellenőrzi, hogy a feladó és a célzott peer **ugyanabban a szobában** van-e
- Ha nem, az üzenet el van dobva, a küldő kap egy `error` választ
- A `to` mező csak `session_id` lehet — nem IP, nem username

### 3. Session validáció

- Signaling üzeneteket **csak jóváhagyott, a szobában lévő** játékosok küldhetnek
- `pending` állapotú játékos (még nem fogadták el) nem kaphat TURN credential-t és nem indíthat signaling-et
- Session_id a szerver által generált UUID — nem guesselhető

### 4. Rate limiting a signaling üzenetekre

- ICE candidate flood megakadályozása: max **30 ice-candidate** event/perc/session
- Ha egy session túllépi, az adott event-típust az adott sessiontől 60 másodpercig ignorálja a szerver (nem bontja a kapcsolatot, csak elveti)

### 5. Payload validáció

- `offer` / `answer`: kötelező `sdp` string mező, max 8192 byte
- `ice-candidate`: kötelező `candidate` string, `sdpMid`, `sdpMLineIndex` — típus ellenőrzés
- `to` mező: UUID regex validáció
- Ismeretlen mezők: silent drop (nem kerülnek tovább)

### 6. Mikrofon engedély kezelése

- `getUserMedia()` csak akkor hívódik meg, amikor a játékos **jóváhagyással bekerül a szobába**
- Ha a felhasználó megtagadja a mikrofon engedélyt → silent fail, a játék tovább fut hang nélkül
- Nincs kötelező mikrofon a játékhoz

---

## Új fájlok

| Fájl | Feladata |
|---|---|
| `server/voice.py` | TURN credential generáló + signaling üzenet router |
| `static/js/voice.js` | WebRTC peer kezelő, getUserMedia, mute/unmute |

---

## Módosított fájlok

| Fájl | Változás |
|---|---|
| `server/main.py` | `/api/voice/credentials` endpoint + voice signaling ágak a WS handlerbe |
| `static/room.html` | Mute/unmute gomb a lobby képernyőn |
| `static/js/lobby.js` | VoiceManager inicializálás belépéskor |
| `static/game.html` | Mute/unmute gomb a játék képernyőn (kis ikon) |
| `static/js/game.js` | VoiceManager importálás, mute állapot perzisztálása |
| `config.json` | TURN szerver adatok (host, port) — a secret NEM itt tárolódik |

---

## Backend részletek — `server/voice.py`

```python
# Fő felelősségek:

def generate_turn_credentials(session_id: str) -> dict:
    """
    HMAC temporal credential coturn-höz.
    Olvassa a TURN_SECRET env változót.
    Visszatér: { username, credential, ttl_seconds, uris }
    """

def validate_signaling_payload(event: str, msg: dict) -> bool:
    """
    Ellenőrzi a beérkező signaling üzenet mezőit és méretét.
    """

async def route_signal(room_code: str, from_sid: str, msg: dict, manager) -> None:
    """
    Továbbítja az offer/answer/ice-candidate üzenetet a célzott peernek.
    Ellenőrzi: from_sid és to_sid ugyanabban a szobában van-e.
    """
```

### TURN_SECRET kezelése

- Az env változóból jön: `TURN_SECRET=...` a systemd service file-ban (`EnvironmentFile=`)
- Nincs commitolva, nincs `config.json`-ban
- Ha hiányzik: a `/api/voice/credentials` 503-mal tér vissza, a hang szimplán nem indul el

---

## Frontend részletek — `static/js/voice.js`

```javascript
class VoiceManager {
  constructor(ws, sessionId)
  
  async init()          // getUserMedia, majd peer-ek felépítése
  async connectToPeer(remoteSid)   // RTCPeerConnection + offer küldés
  handleOffer(msg)      // answer létrehozása + visszaküldés
  handleAnswer(msg)     // setRemoteDescription
  handleIceCandidate(msg) // addIceCandidate
  
  mute()    // localStream.getAudioTracks()[0].enabled = false
  unmute()  // localStream.getAudioTracks()[0].enabled = true
  toggleMute()
  
  cleanup() // minden peer zárása, stream stop
}
```

### Peer kapcsolat lifecycle

1. Játékos belép jóváhagyott szobába → `VoiceManager.init()`
2. Lekéri TURN credential-t (`/api/voice/credentials`)
3. Minden jelenlévő peerhez `connectToPeer()` — offer küldés WS-en
4. Új játékos érkezésekor (`room_state` event): csak az új peer felé küld offer-t
5. Játékos kilép (`player_disconnected` event): az adott peer connection lezárása

---

## WebSocket signaling üzenetek (meglévő WS csatornán)

### Kliens → Szerver (újak)

| Event | Kötelező mezők | Leírás |
|---|---|---|
| `voice_offer` | `to` (session_id), `sdp` (string, max 8192b) | WebRTC offer küldése peernek |
| `voice_answer` | `to` (session_id), `sdp` (string, max 8192b) | WebRTC answer küldése |
| `voice_ice` | `to` (session_id), `candidate` (string), `sdpMid` (string), `sdpMLineIndex` (int) | ICE candidate |
| `voice_mute_state` | `muted` (bool) | Saját mute állapot broadcast (UI jelzőhöz) |

### Szerver → Kliens (újak)

| Event | Mezők | Leírás |
|---|---|---|
| `voice_offer` | `from`, `sdp` | Áttolt offer |
| `voice_answer` | `from`, `sdp` | Áttolt answer |
| `voice_ice` | `from`, `candidate`, `sdpMid`, `sdpMLineIndex` | Áttolt ICE candidate |
| `voice_mute_state` | `session_id`, `muted` | Egy játékos mute állapota változott |

---

## UI változások

### Lobby (`room.html`)

- A "Csatlakozás jóváhagyva" után (azaz amikor a játékos bekerül a szobába) megjelenik egy **mikrofon ikon gomb** a fejléc sarkában
- Ikonok: 🎤 (aktív) / 🔇 (mute) — CSS-sel, emoji helyett SVG ikon
- Mások mute állapota: kis ikon az adott játékos neve mellett a player listában

### Játék képernyő (`game.html`)

- Ugyanaz a mikrofon gomb, fixálva a képernyő sarkába (a Pontok tab közelébe)
- A mute állapot megmarad lobby ↔ játék váltáskor (VoiceManager singleton, WS eseményen keresztül szinkronizálva)

---

## coturn telepítési lépések (szerver)

```bash
apt install coturn

# /etc/turnserver.conf kulcs beállítások:
listening-port=3478
tls-listening-port=5349
fingerprint
use-auth-secret
static-auth-secret=<TURN_SECRET>   # ugyanaz mint az env változóban
realm=emberisegelleneskartyak.hu
total-quota=100
no-multicast-peers
no-loopback-peers
denied-peer-ip=10.0.0.0-10.255.255.255   # belső hálózat tiltás
denied-peer-ip=192.168.0.0-192.168.255.255
```

**Nginx:** a coturn közvetlenül a 3478/UDP porton hallgat, az nginx nem érinti.

---

## Megvalósítás sorrendje

1. `server/voice.py` — credential generátor + signaling router + rate limiter
2. `server/main.py` — `/api/voice/credentials` endpoint + 4 új WS event ág
3. `static/js/voice.js` — VoiceManager osztály
4. `static/room.html` + `static/js/lobby.js` — mute gomb + VoiceManager init
5. `static/game.html` + `static/js/game.js` — mute gomb game screenen
6. coturn config dokumentáció (`coturn_setup.sh` vagy README kiegészítés)

---

## Mi marad ki szándékosan (out of scope most)

- Szerver oldali hangrögzítés / recording
- Hangszint jelző (vizualizáció) — jövőbeli feature
- Push-to-talk mód — jövőbeli feature
- Játékon belüli hangerő-szabályozás másoknál — jövőbeli feature
