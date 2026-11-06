# lucy-local

**Your Trifo robot vacuum works again, without Trifo.**

Trifo, the company behind the Lucy and Max robot vacuums, went under, and its cloud servers went
with it. The robots were built to do everything through that cloud. When it's unreachable the app
can't reach them, so there's no remote control, camera or map. The hardware still works
fine; it just has nobody to talk to.

lucy-local is a small server you run at home that stands in for Trifo's cloud. There's no need to
open the robot, root it, or flash anything.

## The trick: one DNS change

The robot finds its cloud by name: `eudispatch.trifo.com`, `euiot.trifo.com` and, for the camera,
`pt.lightfieldcamera.com.cn`. If your home network answers those names with the address of your own
server, the robot connects there instead and can't tell the difference:

```
before:  robot ──▶ eudispatch.trifo.com ──▶ Trifo's servers        (gone, robot stuck)
after:   robot ──▶ eudispatch.trifo.com ──▶ your DNS: "it's here" ──▶ lucy-local on your LAN
```

So the whole setup is two wildcard DNS rules on your router, Pi-hole, AdGuard Home or similar
(`*.trifo.com` and `*.lightfieldcamera.com.cn` → your server), plus running lucy-local. The robot
logs in, reports its status, takes commands and streams its camera, all inside your home network.
None of it needs the internet.

## What you get

- **Dashboard:** status and battery, clean/pause/dock/find, suction, volume and voice settings,
  live camera and snapshots, and the robot's schedule.
- **Live map** with the robot's position, heading and dock. Tap the map to send the robot to that
  spot (Trifo Max).
- **Drive page:** a full-screen camera with a thumb joystick, keyboard or game controller, and the
  vacuum on while you drive. The server handles manual mode and stops the robot if the
  connection drops.
- **Home Assistant:** an add-on that runs everything inside HA (with a sidebar panel), and an
  integration with a vacuum entity, live map, camera, battery, suction and go-to-point. An MQTT
  bridge is there too if you prefer that.
- **Control profiles and a command tester:** models differ in small ways, so each control is
  mapped to the command your robot accepts. Built in: the **Trifo Max** (tested) and the **Trifo
  Lucy** (from the upstream research). You can try any command from the Settings page and add the
  ones that work.
- **Talk** through the robot's speaker (Lucy profile only; the Max has no working speaker stream).

## How it works

```
robot ──TLS 7990──▶ dispatch      "your broker is SERVER_IP:17766"
robot ──TLS 17766─▶ MQTT broker   register, auth, status, commands
robot ──RTMP 1935─▶ MediaMTX ──WebRTC──▶ browser   (camera, on demand)
browser ─HTTP 8110─▶ lucy-local web UI / API ──▶ robot
```

- **Dispatch and MQTT:** the robot asks the dispatch service which broker to use and is told
  "this one". It then logs in over MQTT, as it did with Trifo, and that's where status reports
  come in and commands go out.
- **Camera:** when someone opens the camera, lucy-local asks the robot to start its live stream.
  The robot pushes ordinary H.264 over RTMP to its stream host, which DNS also points here.
  [MediaMTX](https://github.com/bluenviron/mediamtx) receives it and serves it to browsers
  (WebRTC) and Home Assistant (RTSP). The camera stops a minute after the last viewer leaves.
- **Everything else** (map, go-to-point, driving, voice settings) uses the same commands the
  official Trifo app sends.

## Setup

### Option 1: Home Assistant add-on

The `ha-addon/` folder runs lucy-local and MediaMTX together as a Supervisor add-on (amd64,
aarch64). Prebuilt images are on `ghcr.io/blakeandreasen-pdc/lucy-local-addon-{arch}`.

1. In the add-on store: **⋮ → Repositories**, add `https://github.com/blakeandreasen-pdc/lucy-local`.
2. Install **Lucy Local**, set **Server IP** to the HA host's LAN address, and start it.
3. **DNS:** point `*.trifo.com` and `*.lightfieldcamera.com.cn` at the HA host.
4. Restart the robot (or wait a minute). It shows up in the **Robot vacuum** sidebar panel.

If 443 is taken on the HA host, clear it under the add-on's **Network** settings; the robot only
uses it for update checks. The add-on's Documentation tab has the details.

### Option 2: Docker Compose

1. **DNS:** on the resolver the robot uses, point these at the docker host:
   - `*.trifo.com`: the cloud (`euiot`, `eudispatch`)
   - `*.lightfieldcamera.com.cn`: the camera push
2. **Configure and start:**
   ```sh
   cd docker
   cp .env.example .env      # set SERVER_IP (this host's LAN IP) and TZ
   docker compose up -d --build
   ```
3. Open `http://<host>:8110`. The robot connects within a minute of the server starting (or right
   away after a reboot).

| Port | For |
|---|---|
| 7990, 17766 (TLS) | the robot's dispatch and MQTT |
| 443 (TLS) | the robot's voice-package / OTA checks (answered with "nothing new") |
| 1935 | the robot's camera stream into MediaMTX |
| 8889, 8189/udp | the camera in the browser (WebRTC) |
| 8554 | the camera over RTSP (`rtsp://<host>:8554/lucy`, used by the Home Assistant integration) |
| 8110 | the web UI and API (`/api/docs` lists every endpoint) |

The robot must already be on your Wi-Fi. If it isn't, see the upstream project's
[Wi-Fi setup without the app](https://github.com/Gophercs/trifo_lucy_re#wifi-setup-without-app).

## Control profiles

Each control is an **action** (for example `reverse`, `set_suction` or `camera_wake`) that sends one or more wire commands:

- `dp`: a smart-speaker data point, e.g. `dp 2 = start`, or `blowerMode:{value}`
- `rx`: an app command, e.g. `MANCTRL` / `ANGLE:4` on key `2`

On **Settings**, **Try a command** sends any command and shows the robot's reply as it arrives (the Max can take 1–2 minutes). Every profile field is editable; saving a built-in profile saves your own copy, so the built-ins stay as shipped and updates can fix them. Actions can take a number or a list of choices, and actions you add appear on the dashboard under **Extra**.

| | Trifo Max | Trifo Lucy |
|---|---|---|
| Reverse | `ANGLE:10` | `ANGLE:8` |
| Suction | app `BLOWER` (rx key 2), applied 1–3 min later; `blowerMode:N` is ignored | `blowerMode:N` |
| Fan while driving | `MANBLOWER` 0–100 (percent; 0 = off) | (untested) |
| Status query | `ALL_INFO` (it answers with both the semicolon and the JSON form; asking for `ALL_INFO_JSON` directly returns nothing) | `ALL_INFO_JSON` |

State codes seen on the Max: **1** = cleaning and **5** = returning (as on the Lucy), **12** and **14** = on the dock (at any charge level), **10** = on the dock after a run, **20** = docked with only the camera on. They're named for what was observed. Rename them in the profile as you learn more; lucy-local logs every state change.

## Things learned the hard way

- **Never answer the robot's "who is my owner" question** (info type 0) with a guess. A wrong user id makes it unbind itself. Only the bind-status question (type 2) is answered.
- **Driving is 8 fixed moves** without root. Each `ANGLE:N` starts a short motion, and holding a direction means resending it every ~200 ms. `ANGLE:0` stops. Manual mode needs `MANCTRL K` every 3 s or the robot drops out after ~14 s.
- **The camera wakes** with `RTMP_THROB` + `STREAM_CONTROL 1` (key 10) every 2.5 s. The robot then pushes 640×480 H.264 over RTMP to `pt.lightfieldcamera.com.cn`. No owner reply is needed.
- **The map** comes from `GET_MAP` on rx key 8: about 10 s later the Max answers with base64 of an uncompressed grid (`ogm\0`, u32 0, u32 width, u32 height, f64 cell size, one byte per cell, occupancy 0–10: 5 unknown, below 5 free, above 5 obstacle), then little-endian doubles: dock x, y, heading and robot x, y, heading (just the robot when there are three). Metres map to cells as `row = (height·cell/2 − x)/cell − 0.5`, `col = (width·cell/2 − y)/cell − 0.5`, the same as the Trifo app's decoder. The Lucy's key-18 map commands get no answer from the Max.
- **Talk** is `START_TALK <id>` on key 14: the robot pulls `app-name/<id>` as AAC over RTMP from its stream host and plays it, while its own live stream runs. The browser's Opus mic is converted by `docker/mediamtx/talk-relay.sh`. On the Max this does nothing audible: it pulls the stream and replies `0,<id>`, but stays silent with AAC at 8, 16 and 44.1 kHz, with MP3, in manual mode, and in both mute states. The Max's own stream has no audio track, and its manual mentions no microphone, so there's no listening.
- **Status reports only arrive on a change**, so the server asks for status whenever its copy is over a minute old. A status request sent within a couple of seconds of the robot logging in is ignored, so the first one waits 8 s and is retried.

## Home Assistant

There are two ways in. Use one, not both, or the robot shows up twice.

### Integration (recommended)

`custom_components/lucy_local` talks to lucy-local's web API, so no MQTT broker is needed. Install it with HACS (add this repo as a custom repository, type Integration) or copy the folder into your HA `config/custom_components/`. Restart HA, then **Settings → Devices & services → Add integration → Lucy Local**, and enter the web UI address (e.g. `http://192.168.1.50:8110`). Needs Home Assistant 2025.1 or newer; tested on 2026.10.

| Entity | What it does |
|---|---|
| Vacuum | start, pause, stop, dock, locate, fan speed (the suction presets), `send_command` |
| Map (image) | the explored map with dock and robot, refreshed by lucy-local's event stream; kept fresh while cleaning |
| Camera | the robot's camera via MediaMTX RTSP (WebRTC or HLS in HA). It takes 10-40 s to start, because the robot has to wake its camera. Stills only come from a running stream, so dashboards don't keep waking the robot |
| Battery, Status | sensors |
| Suction, Volume | sliders |
| Voice language | dropdown (shows the last choice made from HA; the robot doesn't report it) |
| Voice prompts | switch (Max) |

The service `lucy_local.go_to` drives the robot to `x`, `y` (metres from the dock on the lucy-local map) without cleaning. The integration's options take a different camera stream URL if MediaMTX runs elsewhere.

### MQTT bridge

On **Settings → Home Assistant**, enter your MQTT broker (for example the Mosquitto add-on) and turn it on. The robot appears as a vacuum with start, pause, stop, return to dock, locate and fan speed, plus a battery sensor. The fan speeds come from the suction presets. `vacuum.send_command` runs any non-driving action from the active profile, e.g. `{"command": "set_volume", "params": {"value": 40}}`.

## Behind a reverse proxy

All the pages' URLs are relative and the camera's signalling goes through lucy-local (`/api/camera/whep`), so one proxy rule (plus the WebSocket upgrade) is enough, on any path prefix:

```nginx
location /api/drive/ws { proxy_pass http://<host>:8110; proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; }
location / { proxy_pass http://<host>:8110; proxy_buffering off; }
```

The video itself goes straight from the browser to `SERVER_IP:8189/udp`, so viewers need a route to that address (LAN or VPN). The same goes for Talk audio.

## Security

There's **no login**. Anyone who can reach the web UI or the camera ports (8888, 8889, 8554) can drive the robot or watch its camera. Keep it on your LAN, or put it behind a proxy that restricts who can reach it. The robot-facing ports use a self-signed certificate, because the robot doesn't verify it.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
SERVER_IP=127.0.0.1 DATA_DIR=./data HTTPS_PORT=0 WEB_PORT=8080 .venv/bin/python -m lucy_local
```

GitHub Actions builds the add-on image on every push that touches the server; bump `version` in `ha-addon/config.yaml` to ship it to Home Assistant.

## Credits and thanks

lucy-local stands on other people's reverse-engineering. None of it would exist without them.

- **[Gophercs/trifo_lucy_re](https://github.com/Gophercs/trifo_lucy_re)** by **Chloe
  ([@Gophercs](https://github.com/Gophercs))** and Claude. They worked out the Trifo cloud
  protocol on a Lucy: the dispatch and MQTT login, the command channel, status reports, manual
  driving, the camera stream and where it goes, and the fact that a DNS redirect is all it takes.
  Their project also covers what needs root (full map editing, both cameras, the microphone) and
  Wi-Fi setup without the app. If you have a Lucy, start there.
- **Victor Drijkoningen**, for the earlier
  [Trifo Max reverse-engineering](https://github.com/VictorDrijkoningen/trifo-robotics-rev-eng).
- **Reddit u/ThatsALovelyShirt**, for
  [prior Trifo Max work](https://www.reddit.com/r/RobotVacuums/comments/1d1120l/comment/m0m11og),
  and the **r/RobotVacuums** community for keeping these robots alive.
- [MediaMTX](https://github.com/bluenviron/mediamtx), which carries the camera stream.

lucy-local is a separate implementation written from that published knowledge, not a copy of the
upstream code, and adds what was found on the Max (map without root, go-to-point, the Max's
command differences). The Max findings are written up for upstream in
[a fork of their repo](https://github.com/blakeandreasen-pdc/trifo_lucy_re/blob/max-notes/docs/trifo_max.md).

Not affiliated with or endorsed by Trifo. Use at your own risk.
