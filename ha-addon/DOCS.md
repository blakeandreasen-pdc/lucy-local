# Lucy Local

Runs lucy-local and its camera server (MediaMTX) in one add-on.

1. Set **Server IP** to this Home Assistant host's LAN address and start the add-on.
2. Point your DNS at that address: `*.trifo.com` and `*.lightfieldcamera.com.cn`
   (see `docs/dns-takeover.md` in the repository). Reboot the robot.
3. Open the web UI, and add the **Lucy Local** integration with `http://<server ip>:8110`.

If port 443 is already used on this host (e.g. by an SSL proxy add-on), clear it under
**Network**: the robot only uses it for update checks.

There is no login on the web UI or camera ports. Keep them on your LAN.
