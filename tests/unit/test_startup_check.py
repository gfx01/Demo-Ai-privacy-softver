import copy
from pathlib import Path

import pytest
import yaml

from privacy_hub.startup_check import check_config

CONFIG = yaml.safe_load((Path(__file__).parents[2] / "services/litellm/config.yaml").read_text())
ENV = {"LITELLM_MASTER_KEY": "sk-" + "a" * 40, "LITELLM_LOG": "INFO"}


def test_shipped_config_passes():
    assert check_config(CONFIG, ENV) == []


def _mutate(fn):
    cfg = copy.deepcopy(CONFIG)
    fn(cfg)
    return check_config(cfg, ENV)


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda c: c.__setitem__("guardrails", []), "exactly once"),
        (lambda c: c["guardrails"][0]["litellm_params"].__setitem__("default_on", False), "default_on"),
        (lambda c: c["guardrails"][0]["litellm_params"].pop("default_on"), "default_on"),
        (lambda c: c["guardrails"][0]["litellm_params"].__setitem__("mode", "pre_call"), "pre_call and post_call"),
        (lambda c: c["guardrails"][0]["litellm_params"].__setitem__("mode", ["during_call", "post_call"]), "during_call"),
        (lambda c: c["litellm_settings"].__setitem__("turn_off_message_logging", False), "turn_off_message_logging"),
        (lambda c: c["general_settings"].__setitem__("store_prompts_in_spend_logs", True), "store_prompts"),
        (lambda c: c["general_settings"].__setitem__("pass_through_endpoints", [{"path": "/x"}]), "pass_through"),
        (lambda c: c["litellm_settings"].__setitem__("custom_provider_map", []), "custom_provider_map"),
        (lambda c: c["model_list"][0]["litellm_params"].__setitem__("guardrails", ["x"]), "sets its own guardrails"),
        (lambda c: c["guardrails"].append(copy.deepcopy(c["guardrails"][0])), "exactly once"),
    ],
)
def test_unsafe_configs_are_rejected(mutation, expected):
    problems = _mutate(mutation)
    assert any(expected in p for p in problems), problems


def test_weak_master_key_and_debug_logging_rejected():
    assert any("MASTER_KEY" in p for p in check_config(CONFIG, {"LITELLM_MASTER_KEY": "sk-1234"}))
    assert any("DEBUG" in p for p in check_config(CONFIG, {**ENV, "LITELLM_LOG": "DEBUG"}))


# -- local-only mode ----------------------------------------------------------------
LOCAL_ENV = {**ENV, "PRIVACY_HUB_LOCAL_ONLY": "true"}


def test_local_only_rejects_external_provider_entries():
    problems = check_config(CONFIG, LOCAL_ENV)
    for name in ("openai/*", "anthropic/*", "gemini/*"):
        assert any(repr(name) in p for p in problems), problems
    assert not any("mock-gpt" in p or "local-llm" in p or "privacy-canary" in p for p in problems)


def test_local_only_filtered_config_passes_and_keeps_local_models():
    from privacy_hub.local_only import filter_config

    filtered, removed = filter_config(CONFIG, LOCAL_ENV)
    assert removed == ["openai/*", "anthropic/*", "gemini/*"]
    assert "local-llm" in [m["model_name"] for m in filtered["model_list"]]
    assert check_config(filtered, LOCAL_ENV) == []


@pytest.mark.parametrize(
    "params, env_extra, local",
    [
        ({"model": "openai/x", "api_base": "http://local-llm:8080/v1"}, {}, True),
        ({"model": "openai/x", "api_base": "https://api.openai.com/v1"}, {}, False),
        ({"model": "openai/x", "api_base": "http://local-llm.attacker.example/v1"}, {}, False),
        ({"model": "hosted_vllm/x", "api_base": "os.environ/VLLM_BASE"}, {"VLLM_BASE": "http://vllm:8000/v1"}, False),
        ({"model": "hosted_vllm/x", "api_base": "os.environ/VLLM_BASE"},
         {"VLLM_BASE": "http://vllm:8000/v1", "PRIVACY_HUB_LOCAL_HOSTS": "vllm"}, True),
        ({"model": "ollama_chat/x"}, {}, False),  # no api_base: LiteLLM's default host
        ({"model": "privacyhub-canary/canary"}, {}, True),
    ],
)
def test_local_model_detection(params, env_extra, local):
    from privacy_hub.local_only import is_local

    assert is_local(params, {**LOCAL_ENV, **env_extra}) is local


def test_local_only_rejects_logging_callbacks():
    from privacy_hub.local_only import filter_config

    cfg, _ = filter_config(copy.deepcopy(CONFIG), LOCAL_ENV)
    cfg["litellm_settings"]["success_callback"] = ["langfuse"]
    assert any("success_callback" in p for p in check_config(cfg, LOCAL_ENV))


@pytest.mark.parametrize("value, on", [("true", True), ("YES", True), (" On ", True), ("1", True), ("false", False), ("", False), ("no", False)])
def test_local_only_enabled_flag_matches_startup_check(monkeypatch, value, on):
    """The entrypoint asks this exit code; startup_check uses the same function."""
    from privacy_hub import local_only

    monkeypatch.setenv("PRIVACY_HUB_LOCAL_ONLY", value)
    assert local_only.main(["local_only", "--enabled"]) == (0 if on else 1)
    assert local_only.enabled({"PRIVACY_HUB_LOCAL_ONLY": value}) is on


def test_init_env_add_missing_keeps_last_line_without_newline(tmp_path):
    import shutil
    import subprocess

    root = Path(__file__).parents[2]
    (tmp_path / "scripts").mkdir()
    shutil.copy(root / "scripts/init-env.sh", tmp_path / "scripts/init-env.sh")
    (tmp_path / ".env.example").write_text("# comment\nA_SECRET=CHANGE_ME\nPUBLIC_URL=http://localhost:3000\nNEW_SECRET=CHANGE_ME\nNEW_SETTING=x\n")
    (tmp_path / ".env").write_text("A_SECRET=keep\nPUBLIC_URL=http://localhost:3000")  # no final newline
    subprocess.run(["sh", str(tmp_path / "scripts/init-env.sh"), "--add-missing"], check=True, capture_output=True)
    lines = (tmp_path / ".env").read_text().splitlines()
    assert lines[:2] == ["A_SECRET=keep", "PUBLIC_URL=http://localhost:3000"]
    assert lines[2].startswith("NEW_SECRET=") and len(lines[2]) > len("NEW_SECRET=") + 20
    assert lines[3] == "NEW_SETTING=x"
