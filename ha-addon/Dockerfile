# Home Assistant add-on image: lucy-local plus MediaMTX in one container.
# Built from the repository root by .github/workflows/addon.yml:
#   docker buildx build -f ha-addon/Dockerfile --platform linux/amd64,linux/arm64 .
FROM python:3.12-slim

ARG TARGETARCH
ARG MEDIAMTX_VERSION=1.9.3

# openssl: self-signed cert for the robot's TLS; ffmpeg: camera relay and talk audio
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl tzdata ffmpeg wget ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && case "$TARGETARCH" in amd64) A=amd64 ;; arm64) A=arm64v8 ;; *) echo "unsupported $TARGETARCH"; exit 1 ;; esac \
 && wget -q -O /tmp/mtx.tgz "https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_${A}.tar.gz" \
 && mkdir /mediamtx-dist && tar -xzf /tmp/mtx.tgz -C /mediamtx-dist && rm /tmp/mtx.tgz \
 && mv /mediamtx-dist/mediamtx /usr/local/bin/mediamtx && mv /mediamtx-dist/mediamtx.yml /mediamtx.yml \
 && rm -rf /mediamtx-dist

WORKDIR /app
COPY pyproject.toml ./
COPY lucy_local/ ./lucy_local/
RUN pip install --no-cache-dir .

COPY docker/mediamtx/robot-cam.sh docker/mediamtx/talk-relay.sh /
COPY ha-addon/run.sh /run.sh
RUN chmod 755 /run.sh /robot-cam.sh /talk-relay.sh

# MediaMTX settings, as in docker/docker-compose.yml but everything on localhost
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 \
    LUCY_URL=http://127.0.0.1:8080 \
    MEDIAMTX_WEBRTC=http://127.0.0.1:8889 \
    MTX_LOGLEVEL=info \
    MTX_API=yes \
    MTX_APIADDRESS=127.0.0.1:9997 \
    MTX_PATHS_LUCY_RUNONDEMAND=/robot-cam.sh \
    MTX_PATHS_LUCY_RUNONDEMANDSTARTTIMEOUT=40s \
    MTX_PATHS_LUCY_RUNONDEMANDCLOSEAFTER=10s \
    MTX_PATHS_TALK_RUNONREADY=/talk-relay.sh \
    MTX_PATHS_TALK_RUNONREADYRESTART=yes

LABEL io.hass.type="addon" io.hass.name="Lucy Local"
CMD ["/run.sh"]
