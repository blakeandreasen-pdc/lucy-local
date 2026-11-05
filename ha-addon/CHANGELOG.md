# Changelog

## 0.1.2

- Drive page: **Vacuum** selector (Off, Low, Medium, High, or the <kbd>V</kbd> key) turns the
  fan on while you drive. It uses the app's manual-mode fan command (`MANBLOWER`), which isn't
  confirmed on a real robot yet, and goes off when manual mode ends

## 0.1.1

- The web UI opens inside Home Assistant (sidebar **Robot vacuum**, and **Open Web UI**)
  through ingress, so it works over HTTPS and remote access too
- The camera's connection setup goes through lucy-local, so no extra proxy rule is needed

## 0.1.0

- First add-on release: lucy-local and MediaMTX in one container (amd64, aarch64)
