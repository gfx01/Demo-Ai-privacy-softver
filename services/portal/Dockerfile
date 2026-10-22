# Portal: admin pages, preview-before-send chat, bootstrap job. Context: repo root.
FROM python:3.12.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY certs/ /tmp/extra-certs/
RUN set -eu; \
    if ls /tmp/extra-certs/*.crt >/dev/null 2>&1; then \
      cp /tmp/extra-certs/*.crt /usr/local/share/ca-certificates/ && update-ca-certificates; \
    fi; \
    rm -rf /tmp/extra-certs
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt
WORKDIR /app
COPY services/portal/requirements.txt /app/requirements.txt
RUN pip install --require-hashes -r /app/requirements.txt
COPY services/portal/portal /app/portal
ENV PYTHONPATH=/app
RUN useradd --system --uid 10003 --home-dir /nonexistent portal
USER 10003
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "portal.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--proxy-headers", "--forwarded-allow-ips", "*"]
