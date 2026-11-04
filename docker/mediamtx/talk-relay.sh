#!/bin/sh
# MediaMTX runOnReady for path "talk": the browser's microphone arrives as Opus over WebRTC;
# the robot only plays AAC over RTMP, so re-encode it to app-name/lltalk (TALK_ID in talk.py).
# After START_TALK the robot pulls that path from its stream host (DNS points it here).
exec ffmpeg -hide_banner -loglevel warning -fflags nobuffer -flags low_delay \
  -rtsp_transport tcp -i rtsp://127.0.0.1:8554/talk \
  -vn -c:a aac -b:a 64k -ar 44100 -ac 1 -f flv rtmp://127.0.0.1:1935/app-name/lltalk
