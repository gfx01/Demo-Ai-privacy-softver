# LiteLLM gateway + privacy_hub guardrail. Build context: repository root.
# The privacy_hub package only needs httpx, cryptography, redis, psycopg and
# pyyaml, which the pinned LiteLLM image already ships - nothing is installed.
FROM litellm/litellm:v1.105.1

# Optional extra CA certificates for outbound provider calls behind a
# TLS-intercepting corporate proxy (./certs/*.crt, empty by default).
COPY certs/ /tmp/extra-certs/
RUN set -eu; \
    cp /etc/ssl/certs/ca-certificates.crt /etc/ssl/privacy-hub-ca.crt; \
    for f in /tmp/extra-certs/*.crt; do [ -f "$f" ] && cat "$f" >> /etc/ssl/privacy-hub-ca.crt; done; \
    rm -rf /tmp/extra-certs
ENV SSL_CERT_FILE=/etc/ssl/privacy-hub-ca.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/privacy-hub-ca.crt

COPY src/privacy_hub /app/privacy/ext/privacy_hub
COPY services/litellm/config.yaml /app/privacy/config.yaml
COPY --chmod=0755 services/litellm/entrypoint.sh /app/privacy/entrypoint.sh
ENV PYTHONPATH=/app/privacy/ext \
    LITELLM_CONFIG=/app/privacy/config.yaml \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    LITELLM_LOG=INFO

HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=6 \
  CMD test -f /tmp/privacy_hub_selftest_ok && python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/privacy/entrypoint.sh"]
CMD []
