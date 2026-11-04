# How a DNS change gives full control of a Trifo robot vacuum

This note explains why pointing a few hostnames at a server on your LAN is enough to take complete control of a Trifo Max or Lucy robot vacuum. Nothing on the robot has to be opened, flashed or rooted. Control here means cleaning, docking, driving, suction, volume and the live camera.

It's written from what was observed on a Trifo **Max** (firmware 3.40.0, EU region) running against [lucy-local](../README.md), and from the earlier reverse-engineering in [Gophercs/trifo_lucy_re](https://github.com/Gophercs/trifo_lucy_re). Anything observed only on a Lucy, or only taken from those notes, is marked as such.

---

## 1. The short version

The robot was designed to talk to Trifo's cloud. Trifo shut down, but the robot still looks up the same hostnames on every boot. Whoever answers those lookups becomes "the cloud".

DNS on its own wouldn't be enough if the robot checked who it was talking to. It doesn't:

| What the robot should verify | What it actually does |
|---|---|
| The TLS certificate of the server | Accepts any certificate, including a self-signed one with the wrong name |
| That the dispatch reply came from Trifo | The reply is only obfuscated with a fixed key and a CRC; there's no signature |
| The MQTT broker it's sent to | Connects to whatever IP:port the dispatch reply names |
| The keys and tokens issued at registration | Accepts arbitrary values (lucy-local hands out the bytes 1–16) |
| That each command is authorised | Commands carry only the robot's own sub-ID, which it reports itself |
| The video service it streams to | Pushes RTMP to whatever `*.lightfieldcamera.com.cn` resolves to |

So an impostor needs exactly three things: answer DNS, present *any* TLS certificate, and speak the (now documented) wire format.

---

## 2. What the robot expects to find

The firmware uses Quanlian's "QLCloud" IoT platform. Four services matter:

| Service | Hostname (EU) | Port | Purpose |
|---|---|---|---|
| Dispatch | `eudispatch.trifo.com` | 7990 TLS | Tells the robot which MQTT broker to use |
| IoT / MQTT | `euiot.trifo.com` (and whatever dispatch returns) | 17766 TLS | Registration, auth, status, commands |
| HTTPS | `*.trifo.com` | 443 TLS | Voice packages and OTA checks |
| Video CDN | `pt.` / `push.lightfieldcamera.com.cn` | 1935 RTMP | The live camera feed for the app |

US robots use `usdispatch.trifo.com` and `usiot.trifo.com` instead. The region is also visible in the dispatch packet (§5.1).

The simplest DNS setup covers everything with two wildcard records:

```
*.trifo.com               -> <server LAN IP>
*.lightfieldcamera.com.cn -> <server LAN IP>
```

The robot uses whatever resolver DHCP gives it, so this goes in the router, Pi-hole, AdGuard Home, dnsmasq or similar.

---

## 3. The boot sequence, step by step

```
 Robot                                        Impostor server (lucy-local)
   │
   │ DNS: eudispatch.trifo.com? ───────────▶ resolver answers <server IP>
   │
   │ TLS :7990 ───────────────────────────▶ any certificate (self-signed is fine)
   │ dispatch packet: "<robot MAC>"        ─▶
   │ ◀─────────────────── dispatch reply: "<server IP>:17766"
   │
   │ TLS :17766 ──────────────────────────▶ any certificate
   │ MQTT CONNECT (client id = MAC) ───────▶
   │ ◀────────────────────────────────────── CONNACK (accept)
   │ SUBSCRIBE d/s/<id> ───────────────────▶ (we reply on this topic)
   │ ◀────────────────────────────────────── SUBACK
   │
   │ PUBLISH register {sn, mac, product_sign} ▶
   │ ◀──────────────── register reply {dev_id, dev_key: anything}
   │ PUBLISH auth {dev_id, dev_sign} ──────▶
   │ ◀──────────────── auth reply {iotid, token: anything, ts}
   │
   │      ── robot is now "online" and obeys this server ──
   │
   │ heartbeats (0x0301) ──────────────────▶ ◀── {"res":{"errcode":0}}
   │ "am I bound?" (0x0303 type 2) ────────▶ ◀── {"t":2,"v":{"b":1}}
   │ "who owns me?" (0x0303 type 0) ───────▶ (never answered; see §6)
   │ status pushes (0x0503) ───────────────▶
   │ ◀────────────────────────────────────── commands (0x0504 / 0x0402)
```

On the test robot the whole handshake took about 30 seconds after the server started, with no power cycle needed. The robot retries the cloud on its own.

---

## 4. Why each step lets an impostor in

### 4.1 TLS without certificate checking

The robot negotiates TLS 1.3, so the traffic is encrypted. But it **doesn't verify the server's certificate**. lucy-local generates a throwaway self-signed certificate for `CN=euiot.trifo.com` on first start, and the robot accepts it on every port. A certificate with the wrong name works just as well.

This is the one check that would have stopped a DNS redirect. Encryption without authentication only protects against passive eavesdropping, not against whoever the robot was redirected to.

### 4.2 Dispatch: obfuscation, not authentication

The dispatch exchange decides where the robot connects next, so it's the most security-critical message. Its format:

```
offset  size  field
0       4     nonce, big-endian; its low byte seeds the cipher (sent in clear)
4       2     magic: 0xF1EB = EU, 0xF1EC = US
6       4     payload length N
10      N     payload, ASCII ("aa:bb:cc:dd:ee:ff" up, "IP:PORT" down)
10+N    2     CRC-16/MODBUS over the plaintext bytes 0..10+N
```

Bytes 4 to `len-4` go through a rolling XOR. Its starting key is the nonce's low byte, sent in the clear, and its step constants come from a seed (`0x7ECF`) that is the same in every robot. There's no secret, and no MAC or signature. The CRC catches accidental corruption, not tampering.

lucy-local's implementation was checked against a real capture: it decodes the Max's request (its MAC) and rebuilds the original server's reply **byte for byte**.

### 4.3 MQTT: the robot authenticates, the broker doesn't

The robot sends an MQTT username and password, so the *broker* could check the robot. Nothing makes the robot check the *broker*. lucy-local accepts every CONNECT and replies with a plain CONNACK.

Every MQTT payload has a 16-byte header followed by JSON:

```
0..3   total length          8..9   flags (0x0100 for our sends)
4..5   protocol 0x0010       10..11 opcode
6..7   command 0x0201        12..15 sequence number
```

| Opcode | Direction | Meaning |
|---|---|---|
| 0x0101 | ⇄ | registration |
| 0x0201 | ⇄ | authentication |
| 0x0301 | ⇄ | heartbeat |
| 0x0303 | ⇄ | info request (owner / bind status) |
| 0x0402 | → robot | app command (`rx`) |
| 0x0403 | → server | reply to an app command |
| 0x0503 | → server | status data points |
| 0x0504 | → robot | smart-speaker command (`downdps`) |
| 0x0B06 | → server | the robot's own diagnostic event log |
| 0x0A01 | → server | "is there a firmware update?" (left unanswered) |

### 4.4 Registration and auth accept anything

At registration the robot sends its serial, MAC and a `product_sign`, an MD5 derived from a per-region product key that is the same for every robot in that region. It expects back a `dev_id` and a `dev_key`. At auth it sends a `dev_sign` and expects an `iotid` and a `token`.

The robot doesn't check any of these values. lucy-local returns its serial as `dev_id`, the bytes `01..10` as the key, and a random token, and the robot goes online. The server is also under no obligation to check the robot's signatures, so there's no exchange anywhere that proves either side's identity.

### 4.5 Commands need no secret

Once online, the robot obeys two kinds of command on its subscribed topic:

**Smart-speaker commands** (opcode 0x0504):
```json
{"data": [{"i": 2, "v": "dock"}], "subid": "<serial minus last char>", "expire": <unix time + 300>}
```

**App commands** (opcode 0x0402, the same channel the Trifo Home app used):
```json
{"data": "{\"head\":\"rq\",\"key\":\"2\",\"cmd\":\"MANCTRL\",\"data\":\"ANGLE:4\",\"sum\":0,\"index\":0,\"requestid\":\"...\"}",
 "ts": <unix time>, "subid": "<serial minus last char>"}
```

The only identifier is `subid`, which the robot gave away in its own registration message. There are no per-command tokens, signatures or replay protection. The `expire` field is honoured, but the server sets it.

### 4.6 The camera follows DNS too

The app's live view worked by having the robot push RTMP to a Chinese CDN. Two app commands, `RTMP_THROB` and `STREAM_CONTROL "1"` (key 10), sent every ~2.5 s, make the robot push H.264 video (640×480, no audio track on the Max) to:

```
rtmp://pt.lightfieldcamera.com.cn/app-name/<stream id>?<signed query>
```

The query string is signed for Trifo's CDN, but that signature is only meaningful if the receiver checks it. Point the hostname at a server running MediaMTX and the stream arrives there. On the test robot this worked with no "owner" reply at all. When the wake commands stop, the robot stops pushing about 15 s later.

---

## 5. Message reference

### 5.1 Dispatch example (real capture, EU)

| | Hex | Decoded |
|---|---|---|
| Request | 29 bytes, `0000` then the encrypted body (hex omitted: it embeds the robot's MAC) | the robot's MAC, magic EU, CRC ok |
| Reply | 31 bytes, encrypted the same way | `<server ip>:17766`, CRC ok |

### 5.2 Status

The robot pushes data point 2 when something changes:

```json
{"status": "12", "battery": "99", "position": "home", "mute": "true", "volume": "87", "blower": "63"}
```

Data point 24 carries versions: `{"bot": "03.40", "firmware": "3.40.0", ...}`.

Because pushes only happen on a change, the server asks for status with app command `ALL_INFO` (key 11). The Max answers twice:

```
ALL_INFO       12;10.017000;4;100;<SSID>;<IP>;<MAC>;63;0;-1;1;-1;-1;0;0
ALL_INFO_JSON  {"clean_state":"12","clean_area":"10.017","clean_time":"4","battery":"100",
                "ssid":"<SSID>","mac":"<MAC>","ip_addr":"<IP>","blower":"63","manual_blower":"0",
                "error_list":"-1","clean_mode":"1",...}
```

Requests sent within ~2 s of the robot logging in are ignored, so lucy-local waits 8 s and retries.

State codes observed on the Max:

| Code | Seen when |
|---|---|
| 1 | cleaning |
| 5 | heading back to the dock |
| 10, 12, 14 | on the dock (at any charge level) |
| 20 | docked with only the camera on |
| 0 | after error 113 while being driven |

### 5.3 Controls that work without root (Max)

| Control | Command | Notes |
|---|---|---|
| Clean / pause / stop / dock / find | `dp 2 = start` / `stop` / `stop` / `dock` / `locate` | replies `CLEAN_STATE OK`, `FIND_ME OK` |
| Volume | `dp 2 = setVolume:N` | reply `VOICE_VOLUME N` |
| Mute toggle | `dp 2 = mute` | |
| Suction | app `BLOWER` = N, key 2 | `blowerMode:N` is **ignored** on the Max; `BLOWER` applies 1–3 minutes later |
| Manual mode | app `MANCTRL` = `S` / `K` (every 3 s) / `E` | without `K` the robot drops out after ~14 s |
| Drive | app `MANCTRL` = `ANGLE:N`, resent every ~200 ms while held | 4 forward, 3/5 gentle, 2/6 turn, 1/7 spin, **10 reverse on the Max** (8 on the Lucy), 0 stop |
| Camera | app `RTMP_THROB` + `STREAM_CONTROL 1`, key 10, every 2.5 s | needs `*.lightfieldcamera.com.cn` redirected |
| Status | app `ALL_INFO`, key 11 | |

The full app command list (decompiled from Trifo Home 2.6.3) lists more, including maps, zones, do-not-disturb and the microphone. Most of these need data only a rooted robot exposes, or haven't been confirmed on the Max.

---

## 6. The one trap: the owner question

Every ~10 s the robot asks the cloud two questions with opcode 0x0303:

- **Type 2, "am I bound?"** Answer `{"t":2,"v":{"b":1}}`.
- **Type 0, "who is my owner?"** **Never answer this one with a guess.** If the user ID in the reply doesn't match the one stored on the robot, the robot logs `DEV_STA_UNBIND` and **unbinds itself** (per the upstream Ghidra analysis of the firmware). Leaving it unanswered is harmless: the robot keeps asking, and everything above still works.

---

## 7. Security implications

This works because the robot trusts the network to tell it who its cloud is. The same property that lets an owner rescue a dead product also lets **anyone who controls DNS for the robot's network** take it over:

- **Who could do it:** a compromised or malicious router, rogue DHCP handing out its own resolver, ARP-spoofed DNS on a flat network, or anything else that can answer the robot's DNS queries.
- **What they get:** everything in §5.3. That includes **the live camera**, and on a Lucy the microphone too (the upstream notes say the RTMP push can carry audio). The status messages also leak the home Wi-Fi SSID, the robot's IP and MAC, and when the house is being cleaned.
- **While Trifo's cloud was alive**, the missing certificate check meant someone on the network path could also sit in the middle of the real cloud connection, not just replace it.
- **No way to notice:** nothing on the robot or in the (now dead) app would show that the cloud had been swapped.

To protect a robot you keep using:

1. Put it on an isolated IoT network or VLAN with no route to the internet. It needs nothing outside your LAN once lucy-local is its cloud.
2. Control DNS for that network yourself, and only resolve `*.trifo.com` and `*.lightfieldcamera.com.cn` to your own server.
3. Treat lucy-local like the camera it controls. It has **no login**, so keep it on the LAN, or put it behind a proxy that allows only trusted clients.
4. Remember the robot can't tell your server from anyone else's. Its security is exactly as good as the network it's on.

---

## 8. Limits and open questions

- **Root-only features:** the full-resolution camera, maps, zones and smooth analogue driving need a rooted robot (UART access).
- **Status codes:** the meanings of codes 10, 12 and 14, and error 113, aren't confirmed; they're named for when they were seen.
- **Manual-mode fan:** `MANBLOWER` wasn't answered while the robot sat on the dock, and hasn't been tested off it.
- **Whether a lost connection ends a run:** the Max went to state 5 shortly after lucy-local restarts twice. Whether losing the cloud makes it abandon a run and go home is unconfirmed.
- **US region:** the US robots should behave the same with the `us*` hostnames and magic `0xF1EC`, but this hasn't been tested here.

---

## Credits

The protocol work behind this note is mostly from [Gophercs/trifo_lucy_re](https://github.com/Gophercs/trifo_lucy_re), plus earlier Trifo research by Victor Drijkoningen and the r/RobotVacuums community. The Max-specific behaviour (reverse is `ANGLE:10`, suction via `BLOWER`, the double `ALL_INFO` reply, the status timing) was found while building and testing lucy-local.
