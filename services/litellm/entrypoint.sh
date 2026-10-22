#!/bin/sh
# Gateway entrypoint: static privacy check -> LiteLLM -> runtime self-test.
# If either check fails the container exits instead of serving traffic.
set -eu

CONFIG="${LITELLM_CONFIG:-/app/privacy/config.yaml}"

# Local-only mode: drop model entries that would call an external provider;
# startup_check then refuses to start if any remains.
# Python decides whether the mode is on, exactly as startup_check does.
if python3 -m privacy_hub.local_only --enabled; then
  python3 -m privacy_hub.local_only "$CONFIG" /tmp/litellm-local-only.yaml
  CONFIG=/tmp/litellm-local-only.yaml
fi

python3 -m privacy_hub.startup_check "$CONFIG"

litellm --config "$CONFIG" --host 0.0.0.0 --port 4000 --num_workers "${LITELLM_NUM_WORKERS:-1}" &
LITELLM_PID=$!
trap 'kill -TERM "$LITELLM_PID" 2>/dev/null || true' TERM INT

if ! python3 -m privacy_hub.selftest; then
  echo "FATAL: privacy self-test failed - stopping the gateway (fail closed)" >&2
  kill -TERM "$LITELLM_PID" 2>/dev/null || true
  wait "$LITELLM_PID" || true
  exit 3
fi

wait "$LITELLM_PID"
