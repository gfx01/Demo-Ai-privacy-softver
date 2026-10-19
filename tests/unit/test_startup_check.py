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
