# Lucy Local

Lucy Local replaces the Trifo cloud for Trifo robot vacuums. The robot only knows how to
talk to Trifo's servers, so the add-on answers in their place. It runs lucy-local (the
robot's "cloud", web UI and API) and MediaMTX (the camera) together.

Tested on the **Trifo Max**. The **Trifo Lucy** profile follows the upstream research and
is less tested. Other Trifo models may work with a custom control profile.

## Installation

1. Install this add-on and open the **Configuration** tab.
2. Set **Server IP** to this Home Assistant host's LAN address (e.g. `192.168.1.50`).
3. Start the add-on.
4. Set up DNS (next section), then **reboot the robot** (switch it off and on again), so
   it looks its servers up afresh.
5. Open the web UI from the sidebar (**Robot vacuum**) or with **Open Web UI**. The robot
   shows as online within about a minute of booting.
6. Optional: add the **Lucy Local** integration for Home Assistant entities (below).

## DNS

The robot looks up its servers by name. Point both of these at the **Server IP**, on the
DNS server the robot uses (usually your router's, or whatever DHCP hands out):

| Domain | What uses it |
|---|---|
| `*.trifo.com` | the robot's cloud connection (dispatch, MQTT, update checks) |
| `*.lightfieldcamera.com.cn` | the camera stream |

**AdGuard Home** (including the add-on): *Filters → DNS rewrites → Add*, once per domain:
`*.trifo.com` → `192.168.1.50`, and `*.lightfieldcamera.com.cn` → `192.168.1.50`.

**Pi-hole v6**: *Settings → All settings → Miscellaneous → misc.dnsmasq_lines*:

```text
address=/trifo.com/192.168.1.50
address=/lightfieldcamera.com.cn/192.168.1.50
```

**dnsmasq** (OpenWrt, older Pi-hole in `/etc/dnsmasq.d/`): the same two `address=` lines.

**Unbound** (OPNsense, pfSense custom options):

```text
server:
  local-zone: "trifo.com." redirect
  local-data: "trifo.com. A 192.168.1.50"
  local-zone: "lightfieldcamera.com.cn." redirect
  local-data: "lightfieldcamera.com.cn. A 192.168.1.50"
```

Check it from any computer on the same network:
`nslookup eudispatch.trifo.com` should answer with the Server IP.

## Configuration

| Option | Default | What it does |
|---|---|---|
| **Server IP** | (empty, required) | This host's LAN address. The robot is told to connect here, and the camera's WebRTC uses it. |
| **Log level** | `info` | `debug` shows every message to and from the robot. |
| **Capture robot frames** | off | Debugging only: writes every robot message to `/data/capture.jsonl` inside the add-on. Grows about 14 MB an hour while the map is open. |

Everything else (control profile, schedules, suction presets, the MQTT bridge) is set in
the web UI under **Settings**, and kept across updates.

## Network ports

The robot expects its servers on fixed ports, so keep the robot ports as they are.

| Port | Used by | Notes |
|---|---|---|
| 7990/tcp | robot | dispatch, required |
| 17766/tcp | robot | MQTT, required |
| 1935/tcp | robot | camera stream in, required for the camera |
| 443/tcp | robot | update checks only. Clear it if 443 is already used on this host |
| 8110/tcp | you | web UI and API outside Home Assistant; the integration uses it |
| 8889/tcp, 8189/udp | browser | camera over WebRTC |
| 8554/tcp | Home Assistant | camera over RTSP for the integration |
| 8888/tcp | anything | camera over HLS, off by default |

## The web UI

- **Dashboard**: status, camera, map (tap the map to send the robot to that spot), the
  main buttons, suction, volume and voice settings.
- **Drive**: a joystick with the camera; also arrow keys or WASD. The robot stops by
  itself if the page loses contact.
- **Settings**: the control profile (which command each button sends, with a live test
  button), schedules, suction presets, and the optional MQTT bridge.

The camera video goes straight from your browser to UDP 8189 on this host, so watching it
from outside your home needs a VPN back in.

## Home Assistant integration

The add-on ships the robot to Home Assistant through the **Lucy Local** integration in the
same repository (`custom_components/lucy_local`, or install it from HACS as a custom
repository). Add it under *Settings → Devices & services → Add integration → Lucy Local*
with `http://<Server IP>:8110`. You get:

- a **vacuum** (start, pause, stop, dock, locate, fan speed)
- the **map** as an image, refreshed while the robot cleans
- the **camera** (it takes 10 to 40 s to start, while the robot wakes its camera)
- **battery** and **status** sensors, **suction** and **volume** sliders, **voice
  language** and **voice prompts**
- the action `lucy_local.go_to` (x, y in metres from the dock) for automations

The add-on's own MQTT bridge (Settings → Home Assistant in the web UI) is the older way
in. Use one or the other, or the robot appears twice.

## Moving from the Docker version

Stop the old stack first: only one server can answer the robot. Point DNS at this host,
then reboot the robot.

Schedules, profile edits and settings stay with the old install. To bring them over, copy
`settings.json`, `profiles.json`, `schedule.json` and `device.json` from the old `/data`
volume into this add-on's `/data` (for example with `docker cp` from the Advanced SSH &
Web Terminal add-on), then restart the add-on.

## Troubleshooting

**The robot stays offline.** Check that DNS answers with the Server IP, that the robot was
rebooted after the change, and the add-on **Log** tab: within a minute of booting, a
connecting robot logs `dispatch from …`, then `registered …` and `robot online: …`. No
`dispatch` line means the robot isn't reaching this host: check DNS and port 7990.

**No camera picture.** The robot needs 10 to 40 s to start streaming. If it never starts,
check that port 1935 is free on this host and that `*.lightfieldcamera.com.cn` resolves to
the Server IP. If the stream starts but stays black, the browser can't reach UDP 8189.

**The robot says "video surveillance on/off".** That's the robot announcing its camera.
Turn **Voice prompts** off to silence it.

**Never answer the robot's "owner" question.** Lucy Local deliberately ignores it: a wrong
answer makes the robot unbind itself.

## Security

There's no login on port 8110 or the camera ports: anyone who can reach them can drive the
robot and watch its camera. Keep them on your LAN. The sidebar panel goes through Home
Assistant's own login.

## Support

Issues and source: <https://github.com/blakeandreasen-pdc/lucy-local>
