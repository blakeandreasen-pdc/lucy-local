#!/bin/sh
set -e
if [ ! -f /data/cert.pem ]; then
  openssl req -x509 -newkey rsa:2048 -keyout /data/key.pem -out /data/cert.pem \
    -days 3650 -nodes -subj "/CN=euiot.trifo.com" 2>/dev/null
  chmod 600 /data/key.pem
fi
exec python -m lucy_local
