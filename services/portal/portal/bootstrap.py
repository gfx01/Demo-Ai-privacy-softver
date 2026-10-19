"""One-shot, idempotent bootstrap run by docker compose after LiteLLM is healthy.

Creates the LiteLLM team for the default organisation and registers the
virtual key used by Open WebUI (value taken from OPENWEBUI_LITELLM_KEY).
"""

from __future__ import annotations

import os
import sys
import time

from .litellm_admin import LiteLLMAdmin

DEFAULT_ORG = os.environ.get("PRIVACY_HUB_DEFAULT_ORG", "default")


def main() -> int:
    admin = LiteLLMAdmin()
    deadline = time.monotonic() + 300
    while not admin.live():
        if time.monotonic() > deadline:
            print("bootstrap: LiteLLM did not become live", file=sys.stderr)
            return 1
        time.sleep(2)

    team_id = f"org-{DEFAULT_ORG}"
    if admin.team_info(team_id) is None:
        admin.create_team(team_id, "Default organisation", DEFAULT_ORG)
        print(f"bootstrap: created team {team_id}")
    else:
        admin.sync_team_models(team_id)

    webui_key = os.environ.get("OPENWEBUI_LITELLM_KEY", "")
    if webui_key:
        if not webui_key.startswith("sk-") or len(webui_key) < 32:
            print("bootstrap: OPENWEBUI_LITELLM_KEY must start with 'sk-' and be >= 32 chars", file=sys.stderr)
            return 1
        if admin.key_info(webui_key) is None:
            admin.generate_key(team_id=team_id, alias="open-webui", organisation_id=DEFAULT_ORG, key=webui_key)
            print("bootstrap: registered Open WebUI key")
    print("bootstrap: done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
