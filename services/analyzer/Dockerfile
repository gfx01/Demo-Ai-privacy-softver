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
COPY services/analyzer/requirements.txt services/analyzer/install_model.py services/analyzer/fetch_lexicon.py /app/
RUN pip install --require-hashes -r /app/requirements.txt

# spaCy models: pinned release wheels, verified by SHA-256.
ARG EN_MODEL_URL=https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl
ARG EN_MODEL_SHA256=5e6329fe3fecedb1d1a02c3ea2172ee0fede6cea6e4aefb6a02d832dba78a310
RUN python /app/install_model.py "$EN_MODEL_URL" "$EN_MODEL_SHA256"
# Multilingual NER (ANALYZER_HU_NER=spacy:xx_ent_wiki_sm). Small; see the evaluation report.
ARG XX_MODEL_URL=https://github.com/explosion/spacy-models/releases/download/xx_ent_wiki_sm-3.8.0/xx_ent_wiki_sm-3.8.0-py3-none-any.whl
ARG XX_MODEL_SHA256=6f3c4b853852ea9e9d2dc76cc950dddb10a7e4c42d813308caefe6c5e8be2f0a
RUN python /app/install_model.py "$XX_MODEL_URL" "$XX_MODEL_SHA256"

# Optional HuSpaCy (ANALYZER_HU_NER=spacy:hu_core_news_md). NOT tested in this
# repository (huggingface.co was unreachable while it was built). HuSpaCy
# wheels have an invalid filename for pip >= 24.1, hence the rename. Pin the
# SHA-256 once you have downloaded the wheel yourself.
#   docker compose build --build-arg HU_SPACY_MODEL_URL=https://huggingface.co/huspacy/hu_core_news_md/resolve/v3.8.0/hu_core_news_md-any-py3-none-any.whl analyzer
ARG HU_SPACY_MODEL_URL=""
ARG HU_SPACY_MODEL_SHA256="unpinned"
ARG HU_SPACY_WHEEL_NAME="hu_core_news_md-3.8.0-py3-none-any.whl"
RUN if [ -n "$HU_SPACY_MODEL_URL" ]; then \
      python /app/install_model.py "$HU_SPACY_MODEL_URL" "$HU_SPACY_MODEL_SHA256" "$HU_SPACY_WHEEL_NAME" --with-deps; \
    fi

# Hungarian name lexicon (official given-name list, pinned fallback mirror).
RUN python /app/fetch_lexicon.py /app/data/lexicon
ENV ANALYZER_LEXICON_DIR=/app/data/lexicon

# Optional self-built models, e.g. models/hu_nerkor_cnn from
# scripts/train_nerkor.py (ANALYZER_HU_NER=spacy:/app/models/hu_nerkor_cnn).
COPY models/ /app/models/

COPY src/privacy_hub /app/privacy_hub
COPY services/analyzer/privacy_analyzer /app/privacy_analyzer
ENV PYTHONPATH=/app

RUN useradd --system --uid 10001 --home-dir /nonexistent analyzer
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "exec uvicorn privacy_analyzer.app:app --host 0.0.0.0 --port 8000 --workers ${ANALYZER_WORKERS:-1} --no-server-header"]
