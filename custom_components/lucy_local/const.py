"""Constants for the Lucy Local integration."""

DOMAIN = "lucy_local"
CONF_URL = "url"
CONF_STREAM_URL = "stream_url"
RTSP_PORT = 8554
STREAM_PATH = "lucy"
POLL_SECONDS = 60          # full status poll; the event stream pushes changes in between
MAP_NUDGE_SECONDS = 20     # while cleaning, ask lucy-local to keep the map fresh this often
