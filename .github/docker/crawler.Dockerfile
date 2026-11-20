# titan007 filters on the TLS ClientHello fingerprint: OpenSSL 3.x clients get
# their connection closed right after the handshake ("unexpected eof"), while
# OpenSSL 1.1.1 clients are served normally (see rule_engine/ingest/crawl.py).
# GitHub's ubuntu-latest runners ship OpenSSL 3.x, so the crawler/scheduler runs
# inside this image instead. Debian bullseye ships OpenSSL 1.1.1, and the
# python base image already bundles the `openssl` CLI, `ca-certificates` and
# `tzdata`.
#
# Do NOT add `apt-get upgrade` here: bullseye is past LTS and debian-security
# has pruned the upgrade pool, so any attempted upgrade fails with HTTP 404.
FROM python:3.10-slim-bullseye

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
