# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# ffmpeg merges/converts media; rtmpdump fetches rtmp:// streams; fonts are used by some ffmpeg filters;
# tini reaps child processes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rtmpdump fonts-dejavu-core ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
# Headless Chromium for the SPA fallback (BROWSER_FALLBACK); --with-deps adds its system libraries (~1.5 GB).
# Build with INSTALL_BROWSER=false for a much smaller image; the fallback then switches itself off.
ARG INSTALL_BROWSER=true
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
        python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*; \
    fi

COPY bot ./bot
COPY scripts ./scripts
# Extra yt-dlp extractors (found on sys.path; /app is the working directory of `python -m bot`).
COPY yt_dlp_plugins ./yt_dlp_plugins

RUN useradd --create-home --uid 1000 botuser \
    && mkdir -p /data && chown -R botuser:botuser /data /app \
    # /updateytdlp upgrades yt-dlp at runtime, so the bot user owns site-packages for that one package.
    && chown -R botuser:botuser "$(python -c 'import site; print(site.getsitepackages()[0])')"
USER botuser
VOLUME ["/data"]

# The bot writes a heartbeat every 30 s; unhealthy if it stops for 2 minutes.
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import os,sys,time; p=os.path.join(os.environ.get('DATA_DIR','/data'),'heartbeat'); sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<120 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "bot"]
