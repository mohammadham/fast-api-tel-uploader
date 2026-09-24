FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libffi-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

RUN useradd -m -u 1000 tgdrive && mkdir -p /srv/data && chown -R tgdrive /srv
USER tgdrive

ENV TGDRIVE_DATA_DIR=/srv/data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/admin/healthz').status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]
