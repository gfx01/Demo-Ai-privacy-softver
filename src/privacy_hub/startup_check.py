"""Refuse to start the gateway unless the privacy guardrail is mandatory.

Run before LiteLLM starts (see services/litellm/entrypoint.sh)::

    python -m privacy_hub.startup_check /app/config.yaml

Exit code 0 = safe to start, 2 = unsafe configuration (details on stderr).
This is a static check; ``privacy_hub.selftest`` additionally proves at
runtime that masking is actually applied (LiteLLM may skip a guardrail whose
constructor fails, see ``config.FatalConfigError``).
"""

from __future__ import annotations

import os
import sys
from typing import Any

import yaml

from . import local_only
from .config import Config, FatalConfigError

GUARDRAIL_CLASS = "privacy_hub.guardrail.PrivacyHubGuardrail"
CANARY_HANDLER = "privacy_hub.providers.canary"
FORBIDDEN_LOG_LEVELS = {"DEBUG"}


def check_config(cfg: dict[str, Any], env: dict[str, str]) -> list[str]:
    problems: list[str] = []
    litellm_settings = cfg.get("litellm_settings") or {}
    general = cfg.get("general_settings") or {}

    # 1. exactly our guardrail, always on, pre + post call, never during_call
    guardrails = cfg.get("guardrails") or []
    ours = [g for g in guardrails if (g.get("litellm_params") or {}).get("guardrail") == GUARDRAIL_CLASS]
    if len(ours) != 1:
        problems.append(f"config must define the {GUARDRAIL_CLASS} guardrail exactly once (found {len(ours)})")
    for g in ours:
        params = g.get("litellm_params") or {}
        if params.get("default_on") is not True:
            problems.append("privacy guardrail must have default_on: true")
        mode = params.get("mode")
        modes = set(mode if isinstance(mode, list) else [mode])
        if not {"pre_call", "post_call"} <= modes:
            problems.append("privacy guardrail mode must include pre_call and post_call")
        if "during_call" in modes:
            problems.append("privacy guardrail must not use during_call (runs in parallel with the LLM call)")
    if "guardrails" in litellm_settings:
        problems.append("legacy litellm_settings.guardrails is not supported; use top-level guardrails")

    # 2. no prompt/response content in logs or spend logs
    if litellm_settings.get("turn_off_message_logging") is not True:
        problems.append("litellm_settings.turn_off_message_logging must be true")
    if general.get("store_prompts_in_spend_logs") is True:
        problems.append("general_settings.store_prompts_in_spend_logs must not be true")
    if litellm_settings.get("set_verbose") is True or general.get("detailed_debug") is True:
        problems.append("verbose/debug logging prints request bodies; disable it")
    if str(env.get("LITELLM_LOG", "")).upper() in FORBIDDEN_LOG_LEVELS:
        problems.append("LITELLM_LOG=DEBUG prints request bodies; use INFO or higher")

    # 3. routes that bypass chat-completion guardrails must not be configured
    if general.get("pass_through_endpoints"):
        problems.append("general_settings.pass_through_endpoints bypass the guardrail; remove them")

    # 4. canary provider for the runtime self-test
    providers = litellm_settings.get("custom_provider_map") or []
    if not any(p.get("custom_handler") == CANARY_HANDLER for p in providers):
        problems.append(f"custom_provider_map must register {CANARY_HANDLER} (runtime self-test)")

    # 5. per-model guardrail overrides could disable or reorder ours
    for m in cfg.get("model_list") or []:
        if (m.get("litellm_params") or {}).get("guardrails"):
            problems.append(f"model {m.get('model_name')!r} sets its own guardrails; not allowed")

    # 6. local-only mode: no model may call an external provider
    if local_only.enabled(env):
        problems.extend(local_only.problems(cfg, env))

    # 7. secrets present and not demo defaults
    master = env.get("LITELLM_MASTER_KEY", "")
    if len(master) < 32 or master in {"sk-1234", "sk-change-me"}:
        problems.append("LITELLM_MASTER_KEY must be set to a random value of >= 32 characters")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m privacy_hub.startup_check CONFIG.yaml", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    problems = check_config(cfg, dict(os.environ))
    try:
        Config.from_env()
    except FatalConfigError as exc:
        problems.append(f"privacy hub env: {exc}")
    if problems:
        print("REFUSING TO START: unsafe privacy configuration:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    print("privacy startup check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
