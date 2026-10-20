#!/bin/sh
# Create .env from .env.example with fresh random secrets. Refuses to
# overwrite an existing .env.
#   --add-missing: after an upgrade, append the settings that .env.example
#                  has and .env lacks (new secrets get random values).
set -eu
cd "$(dirname "$0")/.."

command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 1; }

hex() { openssl rand -hex 24; }
fill() {
  case "$1" in
    PRIVACY_HUB_MAPPING_KEY=CHANGE_ME) echo "PRIVACY_HUB_MAPPING_KEY=$(openssl rand -base64 32)" ;;
    *=sk-CHANGE_ME) echo "${1%%=*}=sk-$(hex)" ;;
    *=CHANGE_ME) echo "${1%%=*}=$(hex)" ;;
    *) echo "$1" ;;
  esac
}
umask 077

if [ "${1:-}" = "--add-missing" ]; then
  [ -e .env ] || { echo ".env does not exist - run without --add-missing first." >&2; exit 1; }
  added=""
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    if ! grep -q "^${key}=" .env; then
      fill "$line" >> .env
      added="$added $key"
    fi
  done < .env.example
  echo "Added to .env:${added:- nothing}"
  exit 0
fi

if [ -e .env ]; then
  echo ".env already exists - not overwriting (use --add-missing after an upgrade)." >&2
  exit 1
fi
while IFS= read -r line; do
  fill "$line"
done < .env.example > .env
echo "Created .env with random secrets (mode 600)."
echo "Admin login: user '$(grep '^PORTAL_ADMIN_USER=' .env | cut -d= -f2)', password in .env (PORTAL_ADMIN_PASSWORD)."
