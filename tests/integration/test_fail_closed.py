"""Fail-closed behaviour against the running stack.

These tests stop, pause or reconfigure services and restore them afterwards
(about 3 minutes). Every failure mode must end in an error for the client
and NOTHING reaching the LLM provider.
"""

from __future__ import annotations

import json
import time

import pytest
import yaml

from .conftest import ROOT, chat, compose, psql, wait_healthy

pytestmark = pytest.mark.integration

PROMPT = "Kérlek, írj Kovács Péternek a kovacs.peter@example.com címre a szerződésről."


def _assert_blocked(gateway, user_key, mock_provider, code: str, expected_status: int = 503) -> None:
    for stream in (False, True):
        status, _, body = chat(gateway, user_key, PROMPT, stream=stream)
        assert status == expected_status, body
        assert code in json.dumps(body)
    assert mock_provider.received() == [], "provider must not be called when masking cannot be guaranteed"


def _wait_until_ok(gateway, user_key, timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        status, text, body = chat(gateway, user_key, PROMPT)
        if status == 200:
            assert "Kovács Péternek" in text
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"gateway did not recover: {status} {body}")
        time.sleep(2)


def test_analyzer_down_blocks(gateway, user_key, mock_provider):
    compose("stop", "analyzer")
    try:
        _assert_blocked(gateway, user_key, mock_provider, "privacy_hub_unavailable")
    finally:
        compose("start", "analyzer")
        wait_healthy("analyzer")
    _wait_until_ok(gateway, user_key)


def test_analyzer_hanging_times_out_and_blocks(gateway, user_key, mock_provider):
    compose("pause", "analyzer")
    try:
        started = time.monotonic()
        status, _, body = chat(gateway, user_key, PROMPT)
        assert status == 503 and "privacy_hub_unavailable" in json.dumps(body)
        assert time.monotonic() - started < 30  # analyzer timeout is 10 s
        assert mock_provider.received() == []
    finally:
        compose("unpause", "analyzer")
    _wait_until_ok(gateway, user_key)


def test_mapping_store_down_blocks(gateway, user_key, mock_provider):
    compose("stop", "redis")
    try:
        _assert_blocked(gateway, user_key, mock_provider, "privacy_hub_unavailable")
    finally:
        compose("start", "redis")
        wait_healthy("redis")
    _wait_until_ok(gateway, user_key)


def test_audit_log_write_failure_blocks(gateway, user_key, mock_provider):
    psql("privacyhub", "REVOKE INSERT ON audit_events FROM ph_gateway")
    try:
        _assert_blocked(gateway, user_key, mock_provider, "privacy_hub_audit_unavailable")
    finally:
        psql("privacyhub", "GRANT INSERT ON audit_events TO ph_gateway")
    _wait_until_ok(gateway, user_key)


def _config() -> dict:
    return yaml.safe_load((ROOT / "services" / "litellm" / "config.yaml").read_text())


def _no_default_on(c: dict) -> None:
    c["guardrails"][0]["litellm_params"]["default_on"] = False


def _no_guardrail(c: dict) -> None:
    c.pop("guardrails")


def _message_logging(c: dict) -> None:
    c["litellm_settings"]["turn_off_message_logging"] = False


def _pass_through(c: dict) -> None:
    c["general_settings"]["pass_through_endpoints"] = [{"path": "/openai", "target": "https://api.openai.com"}]


@pytest.mark.parametrize("mutate, expected", [
    (_no_default_on, "default_on"),
    (_no_guardrail, "guardrail exactly once"),
    (_message_logging, "turn_off_message_logging"),
    (_pass_through, "pass_through_endpoints"),
])
def test_startup_check_refuses_unsafe_config(tmp_path, mutate, expected):
    cfg = _config()
    mutate(cfg)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    path.chmod(0o644)
    tmp_path.chmod(0o755)
    run = compose("run", "--rm", "--no-deps", "-T", "-v", f"{path}:/cfg/config.yaml:ro",
                  "-e", "LITELLM_CONFIG=/cfg/config.yaml", "litellm", check=False, timeout=120)
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    assert expected in output, output


def test_runtime_selftest_detects_unprotected_gateway(tmp_path):
    """Second line of defence: even if LiteLLM somehow started without the
    guardrail (the static check is bypassed here on purpose), the canary
    self-test sees clear-text data at the provider and fails."""
    cfg = _config()
    cfg.pop("guardrails")
    cfg["general_settings"].pop("database_url", None)
    path = tmp_path / "noguard.yaml"
    path.write_text(yaml.safe_dump(cfg))
    path.chmod(0o644)
    tmp_path.chmod(0o755)
    script = ("unset DATABASE_URL; litellm --config /cfg/noguard.yaml --port 4000 >/dev/null 2>&1 & "
              "python3 -m privacy_hub.selftest; echo SELFTEST_EXIT=$?")
    run = compose("run", "--rm", "--no-deps", "-T", "-v", f"{path}:/cfg/noguard.yaml:ro",
                  "-e", "PRIVACY_HUB_SELFTEST_WAIT_S=150", "--entrypoint", "sh", "litellm", "-c", script,
                  check=False, timeout=240)
    output = run.stdout + run.stderr
    assert "SELFTEST_EXIT=1" in output, output
    assert "provider received clear-text canary data" in output, output
