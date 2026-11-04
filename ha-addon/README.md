# Lucy Local

Keep your Trifo robot vacuum working after the Trifo cloud shut down: this add-on
stands in for Trifo's servers on your own network, so the robot (Max, Lucy) connects to
Home Assistant instead.

![Supports amd64][amd64-shield] ![Supports aarch64][aarch64-shield]

## What you get

- **Control panel inside Home Assistant** (sidebar): clean, pause, dock, find, suction,
  volume, voice settings and schedules
- **Live map** with the robot and dock; tap a spot to send the robot there
- **Live camera** in the browser and in Home Assistant
- **Drive it by hand** from the joystick page (touch, mouse or keyboard)
- **Home Assistant entities** through the bundled Lucy Local integration: vacuum,
  map image, camera, battery, suction, volume and more
- Runs entirely on your LAN; nothing is sent anywhere else

## Before you start

The robot finds its servers by name, so **your DNS has to send two domains to this
Home Assistant host**. The Documentation tab shows how for AdGuard Home, Pi-hole,
dnsmasq and Unbound.

[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
