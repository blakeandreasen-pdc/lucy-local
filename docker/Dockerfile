FROM python:3.12-slim

# openssl: one-time self-signed cert for the robot's TLS (it doesn't verify it)
# tzdata: schedules run in the TZ you set
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -u 1000 -m app \
 && mkdir /data && chown app:app /data

WORKDIR /app
COPY pyproject.toml ./
COPY lucy_local/ ./lucy_local/
RUN pip install --no-cache-dir .

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

USER app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 443 7990 17766 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"
ENTRYPOINT ["/entrypoint.sh"]
