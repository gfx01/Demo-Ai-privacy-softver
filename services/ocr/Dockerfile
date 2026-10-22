# OCR / document text extraction service. Build context: repository root.
# Runs on an internal network only; needs no network access at runtime.
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
COPY services/ocr/requirements.txt services/ocr/requirements-surya.txt services/ocr/fetch_tessdata.py /app/
RUN pip install --require-hashes -r /app/requirements.txt

# Optional Surya engine (licence-restricted weights, needs a GPU model server).
ARG OCR_WITH_SURYA=0
RUN if [ "$OCR_WITH_SURYA" = "1" ]; then pip install -r /app/requirements-surya.txt; fi

# Tesseract language models: tessdata_fast hun + eng, pinned commit + SHA-256.
RUN python /app/fetch_tessdata.py /app/tessdata
ENV OCR_TESSDATA_DIR=/app/tessdata

COPY services/ocr/ocr_service /app/ocr_service
ENV PYTHONPATH=/app \
    HF_HUB_OFFLINE=1 \
    DO_NOT_TRACK=1

RUN useradd --system --uid 10002 --home-dir /nonexistent ocr
USER 10002
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "ocr_service.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header"]
