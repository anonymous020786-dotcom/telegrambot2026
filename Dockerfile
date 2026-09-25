# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

# ffmpeg merges/converts media; fonts are used by some ffmpeg filters; tini reaps child processes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot ./bot
COPY scripts ./scripts

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
