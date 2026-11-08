# ---------- 1. build the web app ----------
# Static output, so build it once on the native runner instead of under emulation per platform
FROM --platform=$BUILDPLATFORM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---------- 2. runtime ----------
FROM python:3.12-slim
ARG VERSION=dev
LABEL org.opencontainers.image.title="Crossfade" \
      org.opencontainers.image.description="Sync YouTube Music, Apple Music and Spotify collections" \
      org.opencontainers.image.version="${VERSION}"
ENV CROSSFADE_VERSION=${VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CROSSFADE_DATA_DIR=/data \
    CROSSFADE_WEB_DIR=/app/web/build
# client IP and scheme come from X-Forwarded-* only when the proxy sits in a private network
ENV FORWARDED_ALLOW_IPS="127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
WORKDIR /app
COPY requirements.txt ./
# Unraid convention: run as nobody:users (99:100) so files in appdata stay editable
RUN pip install --no-cache-dir -r requirements.txt \
 && mkdir -p /data && chown 99:100 /data
COPY crossfade/ ./crossfade/
COPY --from=web /web/build ./web/build
USER 99:100
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4).status==200 else 1)"
# One process on purpose: the scheduler and live progress live in memory.
CMD ["uvicorn", "crossfade.api.app:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers"]
