#!/bin/sh
# MediaMTX runOnDemand for path "lucy". Keeps the robot's RTMP push awake by asking
# lucy-local to send its profile's camera_wake action every 2.5 s, then relays the
# robot's app-name/<uuid> stream into "lucy" (app-name/lltalk is talk audio, not her).
# MediaMTX stops this when nobody watches.
WAKE=${LUCY_URL:-http://lucy-local:8080}/api/camera/wake
wake() {
  while :; do
    wget -q -O /dev/null --post-data='' "$WAKE"
    sleep 2.5
  done
}
wake & WAKER=$!
FF=""
trap 'kill $WAKER $FF 2>/dev/null; exit 0' INT TERM

SRC=""
for i in $(seq 1 30); do
  SRC=$(wget -q -O - http://127.0.0.1:9997/v3/paths/list | grep -o '"name":"app-name/[0-9a-f-]\{36\}"' | head -1 | cut -d'"' -f4)
  [ -n "$SRC" ] && break
  sleep 1
done
if [ -z "$SRC" ]; then echo "robot-cam: the robot never published"; kill $WAKER; exit 1; fi

# the path can be listed a moment before her publish is ready (404), so retry
for i in $(seq 1 20); do
  ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp -i "rtsp://127.0.0.1:8554/$SRC" \
    -c copy -f rtsp "rtsp://127.0.0.1:8554/lucy" &
  FF=$!
  wait $FF
  sleep 1
done
kill $WAKER 2>/dev/null
