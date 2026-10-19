# Analyzer service: Presidio analyzer + anonymizer + privacy_hub masking.
# Build context: repository root.
FROM python:3.12.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Optional extra CA certificates (corporate TLS-intercepting proxies): put
# *.crt files into ./certs before building. Empty by default.
COPY certs/ /tmp/extra-certs/
RUN set -eu; \
    if ls /tmp/extra-certs/*.crt >/dev/null 2>&1; then \
      cp /tmp/extra-certs/*.crt /usr/local/share/ca-certificates/ && update-ca-certificates; \
    fi; \
    rm -rf /tmp/extra-certs
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app
COPY services/analyzer/requirements.txt services/analyzer/install_model.py /app/
RUN pip install --require-hashes -r /app/requirements.txt

# spaCy models: pinned release wheels, verified by SHA-256.
ARG EN_MODEL_URL=https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl
ARG EN_MODEL_SHA256=5e6329fe3fecedb1d1a02c3ea2172ee0fede6cea6e4aefb6a02d832dba78a310
RUN python /app/install_model.py "$EN_MODEL_URL" "$EN_MODEL_SHA256"

COPY src/privacy_hub /app/privacy_hub
COPY services/analyzer/privacy_analyzer /app/privacy_analyzer
ENV PYTHONPATH=/app

RUN useradd --system --uid 10001 --home-dir /nonexistent analyzer
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "exec uvicorn privacy_analyzer.app:app --host 0.0.0.0 --port 8000 --workers ${ANALYZER_WORKERS:-1} --no-server-header"]
