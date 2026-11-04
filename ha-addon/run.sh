#!/bin/bash
# Add-on entry: read the options, then run MediaMTX and lucy-local side by side.
# If either stops, stop the other too, so the Supervisor's watchdog restarts the add-on.
set -euo pipefail

opt() { python3 -c "import json,sys; v=json.load(open('/data/options.json')).get(sys.argv[1]); print('' if v is None else v)" "$1"; }

export SERVER_IP="$(opt server_ip)"
export LOG_LEVEL="$(opt log_level)"
[ "$(opt capture)" = "True" ] && export CAPTURE=1 || export CAPTURE=
if [ -z "$SERVER_IP" ]; then
  echo "Set 'Server IP' in the add-on configuration to this host's LAN address." >&2
  exit 1
fi
export MTX_WEBRTCADDITIONALHOSTS="$SERVER_IP"

if [ ! -f /data/cert.pem ]; then
  openssl req -x509 -newkey rsa:2048 -keyout /data/key.pem -out /data/cert.pem \
    -days 3650 -nodes -subj "/CN=euiot.trifo.com" 2>/dev/null
  chmod 600 /data/key.pem
fi

mediamtx /mediamtx.yml &
MTX=$!
python3 -m lucy_local &
APP=$!
trap 'kill -TERM $MTX $APP 2>/dev/null' TERM INT
wait -n $MTX $APP || true
echo "a process exited; stopping the add-on" >&2
kill -TERM $MTX $APP 2>/dev/null || true
wait || true
exit 1
