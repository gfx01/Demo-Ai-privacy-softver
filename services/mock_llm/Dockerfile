# Mock OpenAI-compatible provider for keyless testing. Build context: repo root.
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
COPY services/mock_llm/requirements.txt /app/requirements.txt
RUN pip install --require-hashes -r /app/requirements.txt
COPY services/mock_llm/app.py /app/app.py
RUN useradd --system --uid 10002 --home-dir /nonexistent mock
USER 10002
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header", "--no-access-log"]
