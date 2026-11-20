# titan007 filters on the TLS ClientHello fingerprint: OpenSSL 3.x clients get
# their connection closed right after the handshake ("unexpected eof"), while
# OpenSSL 1.1.1 clients are served normally (see rule_engine/ingest/crawl.py).
# GitHub's ubuntu-latest runners ship OpenSSL 3.x, so the crawler/scheduler runs
# inside this image instead. Debian bullseye provides OpenSSL 1.1.1 (w).
FROM python:3.10-slim-bullseye

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
