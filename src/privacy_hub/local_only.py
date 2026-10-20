"""Local-only mode: every model must be served inside the compose network.

With ``PRIVACY_HUB_LOCAL_ONLY=true`` the gateway entrypoint writes a copy of
the LiteLLM config without the model entries that would call an external
provider (the ``openai/*`` style wildcards), and ``startup_check`` refuses to
start if any entry still points outside. Together with
docker-compose.local-only.yml, which cuts the gateway's internet access,
prompts cannot leave the server even if a model entry is misconfigured.

Usage (entrypoint)::

    python -m privacy_hub.local_only CONFIG.yaml OUT.yaml
"""

from __future__ import annotations

import os
import sys
from typing import Any
from urllib.parse import urlsplit

import yaml

# Compose service names of model servers on the internal "providers" network.
DEFAULT_LOCAL_HOSTS = frozenset({"mock-llm", "local-llm"})
# In-process LiteLLM providers registered by the privacy hub (no network).
IN_PROCESS_PREFIXES = ("privacyhub-canary/", "privacyhub-preview/")
CALLBACK_KEYS = ("callbacks", "success_callback", "failure_callback", "service_callback")


def enabled(env: dict[str, str]) -> bool:
    return str(env.get("PRIVACY_HUB_LOCAL_ONLY", "")).strip().lower() in {"1", "true", "yes", "on"}


def local_hosts(env: dict[str, str]) -> frozenset[str]:
    extra = {h.strip().lower() for h in env.get("PRIVACY_HUB_LOCAL_HOSTS", "").split(",") if h.strip()}
    return DEFAULT_LOCAL_HOSTS | extra


def model_target(params: dict[str, Any], env: dict[str, str]) -> str | None:
    """Host a model entry sends requests to; None if it would use the
    provider's public default (no api_base)."""
    base = params.get("api_base")
    if isinstance(base, str) and base.startswith("os.environ/"):
        base = env.get(base.split("/", 1)[1], "")
    if not isinstance(base, str) or not base:
        return None
    return (urlsplit(base).hostname or "").lower() or None


def is_local(params: dict[str, Any], env: dict[str, str]) -> bool:
    if str(params.get("model", "")).startswith(IN_PROCESS_PREFIXES):
        return True
    return model_target(params, env) in local_hosts(env)


def problems(cfg: dict[str, Any], env: dict[str, str]) -> list[str]:
    found = []
    for m in cfg.get("model_list") or []:
        params = m.get("litellm_params") or {}
        if not is_local(params, env):
            target = model_target(params, env) or "the provider's public API"
            found.append(f"local-only mode: model {m.get('model_name')!r} would send requests to {target}")
    settings = cfg.get("litellm_settings") or {}
    for key in CALLBACK_KEYS:
        if settings.get(key):
            found.append(f"local-only mode: litellm_settings.{key} may send data to an external service; remove it")
    return found


def filter_config(cfg: dict[str, Any], env: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    kept, removed = [], []
    for m in cfg.get("model_list") or []:
        (kept if is_local(m.get("litellm_params") or {}, env) else removed).append(m)
    out = dict(cfg)
    out["model_list"] = kept
    return out, [str(m.get("model_name")) for m in removed]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m privacy_hub.local_only CONFIG.yaml OUT.yaml", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    out, removed = filter_config(cfg, dict(os.environ))
    with open(argv[2], "w", encoding="utf-8") as fh:
        yaml.safe_dump(out, fh, sort_keys=False, allow_unicode=True)
    print(f"local-only mode: removed external model entries: {', '.join(removed) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
