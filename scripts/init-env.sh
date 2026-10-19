#!/bin/sh
# Create .env from .env.example with fresh random secrets. Refuses to
# overwrite an existing .env.
set -eu
cd "$(dirname "$0")/.."

if [ -e .env ]; then
  echo ".env already exists - not overwriting." >&2
  exit 1
fi
command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 1; }

hex() { openssl rand -hex 24; }
umask 077
while IFS= read -r line; do
  case "$line" in
    PRIVACY_HUB_MAPPING_KEY=CHANGE_ME) echo "PRIVACY_HUB_MAPPING_KEY=$(openssl rand -base64 32)" ;;
    *=sk-CHANGE_ME) echo "${line%%=*}=sk-$(hex)" ;;
    *=CHANGE_ME) echo "${line%%=*}=$(hex)" ;;
    *) echo "$line" ;;
  esac
done < .env.example > .env
echo "Created .env with random secrets (mode 600)."
echo "Admin login: user '$(grep '^PORTAL_ADMIN_USER=' .env | cut -d= -f2)', password in .env (PORTAL_ADMIN_PASSWORD)."
