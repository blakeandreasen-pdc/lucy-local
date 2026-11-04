# lucy-local

A local replacement for the dead Trifo cloud, for Trifo robot vacuums (tested on a **Trifo Max**, with a profile for the **Lucy**). It doesn't need root on the robot.

- **Local cloud:** the robot connects to this server instead of Trifo's (dispatch, MQTT, registration, heartbeats).
- **Dashboard:** status, clean/pause/dock/find, suction, volume and mute, live camera and snapshots.
- **Map:** the robot's map on the dashboard with its position, heading and dock, refreshed every few seconds while it's on screen (Trifo Max, via `GET_MAP`).
- **Talk** (Lucy profile): speak through the robot's speaker from the dashboard or Drive page. Browsers only allow the microphone on HTTPS pages, so use it through an HTTPS proxy. The Max accepts the talk command and downloads the audio but plays nothing, so its profile has no Talk.
- **Drive page:** a full-screen camera with a thumb joystick, WASD/arrow keys, or a game controller. The server switches manual mode on and off and has a dead-man stop.
- **Control profiles:** map each control to the command your model actually accepts, and test it live from the Settings page.
- **Schedules**, with optional suction per run.
- **Home Assistant:** a custom integration (vacuum, live map, camera, battery, suction, volume, voice settings, go-to-point), or the simpler MQTT discovery bridge.

## How it works

```
robot ──TLS 7990──▶ dispatch      "your broker is SERVER_IP:17766"
robot ──TLS 17766─▶ MQTT broker   register, auth, status, commands
robot ──RTMP 1935─▶ MediaMTX ──WebRTC──▶ browser   (camera, on demand)
browser ─HTTP 8110─▶ lucy-local web UI / API ──▶ robot
```

DNS sends the robot's cloud names to this host, so it never knows the difference.

## Setup

1. **DNS:** on the resolver the robot uses, point these at the docker host:
   - `*.trifo.com`: the cloud (`euiot`, `eudispatch`)
   - `*.lightfieldcamera.com.cn`: the camera push
2. **Configure and start:**
   ```sh
   cd docker
   cp .env.example .env      # set SERVER_IP (this host's LAN IP) and TZ
   docker compose up -d --build
   ```
3. Open `http://<host>:8110`. The robot connects within a minute of the server starting (or right away after a reboot).

| Port | For |
|---|---|
| 7990, 17766 (TLS) | the robot's dispatch and MQTT |
| 443 (TLS) | the robot's voice-package / OTA checks (answered with "nothing new") |
| 1935 | the robot's camera stream into MediaMTX |
| 8889, 8189/udp | the camera in the browser (WebRTC) |
| 8554 | the camera over RTSP (`rtsp://<host>:8554/lucy`, used by the Home Assistant integration) |
| 8110 | the web UI and API (`/api/docs` lists every endpoint) |

### As a Home Assistant add-on

The `ha-addon/` folder runs lucy-local and MediaMTX together as a Supervisor add-on (amd64, aarch64). GitHub Actions builds the image into `ghcr.io/blakeandreasen-pdc/lucy-local-addon-{arch}` on every push that touches the server.

1. In the add-on store: **⋮ → Repositories**, add `https://github.com/blakeandreasen-pdc/lucy-local`.
2. Install **Lucy Local**, set **Server IP** to the HA host's LAN address, and start it.
3. Point the DNS entries at the HA host instead, and reboot the robot.

The web UI opens inside Home Assistant (sidebar entry, through ingress); it's also on port 8110, which the integration uses. The add-on uses the same robot and camera ports as the compose stack. If 443 is taken on the HA host, clear it under the add-on's **Network** settings; the robot only uses it for update checks. Bump `version` in `ha-addon/config.yaml` to ship a new build to HA.

## Control profiles

Each control is an **action** (for example `reverse`, `set_suction` or `camera_wake`) that sends one or more wire commands:

- `dp`: a smart-speaker data point, e.g. `dp 2 = start`, or `blowerMode:{value}`
- `rx`: an app command, e.g. `MANCTRL` / `ANGLE:4` on key `2`

The built-in profiles are read-only. On **Settings**, duplicate one, change what you need, press **Test** to send one command and see the robot's reply, then save and switch to it.

| | Trifo Max | Trifo Lucy |
|---|---|---|
| Reverse | `ANGLE:10` | `ANGLE:8` |
| Suction | app `BLOWER` (rx key 2), applied 1–3 min later; `blowerMode:N` is ignored | `blowerMode:N` |
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

## Credits

The protocol knowledge comes from the reverse-engineering work in [Gophercs/trifo_lucy_re](https://github.com/Gophercs/trifo_lucy_re) and earlier Trifo research by Victor Drijkoningen and the r/RobotVacuums community. lucy-local is a fresh implementation of that knowledge, not a copy of their code. It isn't affiliated with Trifo.
