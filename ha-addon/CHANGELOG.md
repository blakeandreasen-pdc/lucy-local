# Changelog

## 0.1.5

- Settings: **Wi-Fi setup** card. Enter your Wi-Fi name and password and it shows the QR code
  the robot reads in network-configuration mode, the same code the Trifo app made. It's
  generated in your browser (bundled qrcode-generator, MIT); the password isn't sent or saved.
  Tap it for full screen

## 0.1.4

- Settings: new **Try a command** card. Send any rx or dp command without editing a profile,
  start from one of the profile's commands, and watch replies arrive (the one you're waiting
  for turns green). **Add to profile…** turns a command that works into an action
- Profiles: everything is editable, including built-ins. Saving a built-in saves your own copy
  and offers to switch to it. Actions can take no value, a number range or a list of choices,
  and **+ Add action** creates your own actions, which show on the dashboard under **Extra**

## 0.1.3

- Drive page **Vacuum** levels fixed. The robot takes the fan as a percentage (0-100), not
  the -1/0/1/2 levels used in 0.1.2, so Off ran the fan at full and Medium/High barely ran it.
  Levels are now Off 0 %, Low 30 %, Medium 60 %, High 100 %

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
