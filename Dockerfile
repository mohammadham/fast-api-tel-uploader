FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Build deps only. ffmpeg is intentionally NOT installed via apt: the
# imageio-ffmpeg wheel carries a static binary, so video thumbnails work
# without adding ~100 MB to the image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libffi-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
# scripts/healthcheck.py runs the deep check (sessions decrypt, DB integrity)
COPY scripts ./scripts

RUN useradd -m -u 1000 tgdrive && mkdir -p /srv/data && chown -R tgdrive /srv
USER tgdrive

ENV TGDRIVE_DATA_DIR=/srv/data
EXPOSE 8000

# /readyz rather than /healthz: healthz only proves the DB answers, readyz also
# requires the queue and the account pool to have finished starting, so a
# half-booted container is restarted instead of left serving.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/admin/readyz').status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]
