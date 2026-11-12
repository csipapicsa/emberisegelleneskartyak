# Voice chat: design

## Summary

Browser-to-browser voice over a WebRTC peer-to-peer mesh. The **server never decodes, mixes or processes
audio**: it only relays the messages needed to set up the connections (signaling), and provides a TURN relay
for clients behind NAT.

---

## Architecture

```
[Player A] ──── Opus audio (WebRTC P2P) ────── [Player B]
     │                                               │
     │          coturn TURN (when needed)            │
     └──────────────────────────────────────────────┘

[FastAPI WS] = signaling only (offer / answer / ICE candidate)
             = inside the existing /ws/<code> channel
```

### Why a P2P mesh and not an SFU?

- At most 5 players per voice room → 10 peer connections in total: a trivial load, also for the clients.
- The server **never sees the audio**, not even through coturn (it relays encrypted packets, it can't decrypt).
- No separate Janus / mediasoup daemon: nothing extra to install next to the Python service.
- coturn itself uses negligible CPU (it only forwards UDP packets).

---

## Security model

### 1. Time-limited TURN credentials (the most important part)

coturn's **HMAC temporal credentials**:
- The server issues a short-lived (e.g. 24-hour) username/password pair, derived with HMAC-SHA1.
- The client only gets this credential; the shared secret **never leaves the server**.
- coturn refuses TURN allocations with an expired credential.
- No permanent password exists that could leak and be abused.

```
username = timestamp:session_id   (e.g. "1745000000:abc123")
password = HMAC-SHA1(secret, username)
```

Endpoint: `GET /api/voice/credentials`, only for a player in an open room (and rate-limited per IP).

### 2. Signaling isolated per room

- Signaling messages (offer, answer, ICE candidate) travel over the existing WebSocket.
- The server checks that the sender and the target peer are **in the same room**; otherwise the message is
  dropped.
- The `to` field can only be a `session_id`: not an IP, not a username.

### 3. Session validation

- Only **approved players in the room** may send signaling messages.
- A pending player (not yet approved) gets no TURN credential and can't start signaling.
- Session IDs are server-generated UUIDs: not guessable.

### 4. Rate limiting signaling

- Against ICE candidate floods: at most **30 ICE candidate** events per minute per session.
- Over the limit, the server ignores that event type from that session for 60 seconds (the connection stays,
  the messages are dropped).

### 5. Payload validation

- `offer` / `answer`: a required `sdp` string, at most 8192 bytes.
- ICE candidate: required `candidate` string, `sdpMid`, `sdpMLineIndex`, type-checked.
- `to`: UUID regex.
- Unknown fields: silently dropped (never forwarded).

### 6. Microphone permission

- `getUserMedia()` is only called once the player has been **approved into the room**.
- If the user denies the microphone → silent fallback; the game goes on without voice.
- A microphone is never required to play.

---

## Files

| File | Role |
|---|---|
| `server/voice.py` | TURN credential generator, signaling validation and routing, rate limiter |
| `static/js/voice.js` | WebRTC peer manager, getUserMedia, mute/unmute |
| `server/main.py` | `/api/voice/credentials` and the voice signaling branches of the WebSocket handler |
| `static/room.html`, `static/js/lobby.js` | Mute/unmute button in the lobby, VoiceManager start on approval |
| `static/game.html`, `static/js/game.js` | Mute/unmute button on the game screen, mute state kept |
| `config.json` | TURN server host and port; the secret is **not** stored here |

Computer players (server/bot.py) have no microphone: the clients leave them out of the voice mesh.

---

## Backend: `server/voice.py`

```python
def generate_turn_credentials(session_id: str) -> dict:
    """HMAC temporal credential for coturn, from the TURN_SECRET environment variable.
    Returns {username, credential, ttl_seconds, uris}."""

def validate_signaling_payload(event: str, msg: dict) -> bool:
    """Checks the fields and sizes of an incoming signaling message."""

async def route_signal(room_code: str, from_sid: str, msg: dict, manager) -> None:
    """Forwards an offer/answer/ICE candidate to the target peer, if both are in the same room."""
```

### TURN_SECRET

- From the environment: `TURN_SECRET=...` via the systemd unit's `EnvironmentFile=`.
- Not committed, not in `config.json`.
- If missing, `/api/voice/credentials` answers 503 and voice simply doesn't start.

---

## Frontend: `static/js/voice.js`

```javascript
class VoiceManager {
  constructor(ws, sessionId)

  async init()                     // getUserMedia, then connect to the peers
  async connectToPeer(remoteSid)   // RTCPeerConnection + send an offer
  handleOffer(msg)                 // create and send back an answer
  handleAnswer(msg)                // setRemoteDescription
  handleIceCandidate(msg)          // addIceCandidate

  mute()      // localStream.getAudioTracks()[0].enabled = false
  unmute()    // localStream.getAudioTracks()[0].enabled = true
  toggleMute()

  cleanup()   // close every peer, stop the stream
}
```

### Peer connection lifecycle

1. A player enters an approved room → `VoiceManager.init()`.
2. It fetches a TURN credential (`/api/voice/credentials`).
3. `connectToPeer()` for every present peer: an offer over the WebSocket.
4. When a new player arrives (`room_state`): an offer to the new peer only.
5. When a player leaves (`player_disconnected`): that peer connection is closed.

---

## WebSocket signaling messages (on the existing channel)

### Client → server

| Event | Required fields | Meaning |
|---|---|---|
| `voice_offer` | `to` (session_id), `sdp` (string, max 8192 B) | WebRTC offer to a peer |
| `voice_answer` | `to` (session_id), `sdp` (string, max 8192 B) | WebRTC answer |
| `voice_ice` | `to`, `candidate` (string), `sdpMid` (string), `sdpMLineIndex` (int) | ICE candidate |
| `voice_mute_state` | `muted` (bool) | Own mute state, broadcast for the UI |

### Server → client

| Event | Fields | Meaning |
|---|---|---|
| `voice_offer` | `from`, `sdp` | Forwarded offer |
| `voice_answer` | `from`, `sdp` | Forwarded answer |
| `voice_ice` | `from`, `candidate`, `sdpMid`, `sdpMLineIndex` | Forwarded ICE candidate |
| `voice_mute_state` | `session_id`, `muted` | A player's mute state changed |

---

## UI

### Lobby (`room.html`)

- Once a player is approved into the room, a **microphone button** appears in the header corner.
- 🎤 (on) / 🔇 (muted).
- Other players' mute state: a small icon next to their name in the player list.

### Game screen (`game.html`)

- The same microphone button, fixed in the screen corner (near the scores tab).
- The mute state carries over between lobby and game.

---

## coturn setup (server)

```bash
# /etc/turnserver.conf, the key settings:
listening-port=3478
tls-listening-port=5349
fingerprint
use-auth-secret
static-auth-secret=<TURN_SECRET>   # the same value as the environment variable
realm=emberisegelleneskartyak.hu
total-quota=100
no-multicast-peers
no-loopback-peers
denied-peer-ip=10.0.0.0-10.255.255.255     # no relaying into private networks
denied-peer-ip=192.168.0.0-192.168.255.255
```

**nginx:** coturn listens on 3478/UDP directly; nginx isn't involved.

---

## Deliberately out of scope

- Server-side recording.
- A speaking-level indicator (a possible future feature).
- Push-to-talk (future).
- Per-player volume control (future).
