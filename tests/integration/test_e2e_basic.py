"""End-to-end: client -> nginx -> LiteLLM + guardrail -> mock provider."""

import json

import pytest

from .conftest import chat, compose

pytestmark = pytest.mark.integration

NAME = "Kovács Péter"
EMAIL = "kovacs.peter@example.org"
PHONE = "+36 30 000 4567"
PROMPT = f"Kérlek, írj {NAME}nek a {EMAIL} címre, vagy hívd a {PHONE} számon a szerződés miatt."


def _assert_no_pii(bodies: list[dict], *values: str) -> None:
    dumped = json.dumps(bodies, ensure_ascii=False)
    for v in values:
        assert v not in dumped, f"provider received clear-text {v!r}"


def test_gateway_selftest_passed():
    logs = compose("logs", "litellm").stdout
    assert "privacy startup check passed" in logs
    assert "privacy self-test passed" in logs


def test_non_streaming_masks_and_restores(gateway, user_key, mock_provider):
    status, text, _ = chat(gateway, user_key, PROMPT)
    assert status == 200
    assert text == f"Mock reply: {PROMPT}"
    received = mock_provider.received()
    assert len(received) == 1
    _assert_no_pii(received, NAME, EMAIL, PHONE, "000 4567", "Kovács", "Péter")
    sent = received[0]["messages"][0]["content"]
    assert "[PERSON_1]-nek" in sent and "[EMAIL_1]" in sent and "[PHONE_1]" in sent
    # nothing but the model and the (masked) messages reaches the provider
    assert set(received[0]) <= {"model", "messages", "stream", "stream_options", "user"}


def test_streaming_masks_and_restores(gateway, user_key, mock_provider):
    status, text, _ = chat(gateway, user_key, PROMPT, model="mock-inflect", stream=True)
    assert status == 200
    assert text.startswith(f"Mock válasz: {PROMPT}")
    # the mock re-used "[PERSON_1]" with a back-harmony dative ("-nak");
    # restoration must produce the correct form of the original name
    assert "Írtam Kovács Péternek." in text
    assert "[PERSON_1]" not in text and "[EMAIL_1]" not in text
    _assert_no_pii(mock_provider.received(), NAME, EMAIL, PHONE, "Péter")


def test_user_field_is_pseudonymised(gateway, user_key, mock_provider):
    status, _, _ = chat(gateway, user_key, "Hello", user="alice@corp.example")
    assert status == 200
    _assert_no_pii(mock_provider.received(), "alice@corp.example")


@pytest.mark.parametrize(
    "path",
    [
        "/v1/embeddings",
        "/v1/responses",
        "/v1/messages",
        "/v1/completions",
        "/anthropic/v1/messages",
        "/openai/v1/chat/completions",
        "/gemini/v1beta/models/x:generateContent",
        "/v1/audio/transcriptions",
        "/v1/files",
        "/key/generate",
        "/ui",
        "/mcp",
        "/v1/chat/completions/",
        "/V1/chat/completions",
    ],
)
def test_non_allowlisted_routes_are_unreachable(gateway, master_key, path):
    r = gateway.post(path, headers={"Authorization": f"Bearer {master_key}"}, json={"model": "mock-gpt", "input": "x"})
    assert r.status_code == 404


def test_canary_model_not_available_to_users(gateway, user_key):
    status, _, _ = chat(gateway, user_key, "hi", model="privacy-canary")
    assert status in (401, 403)


def test_preview_model_returns_masked_text(gateway, user_key, mock_provider):
    status, text, _ = chat(gateway, user_key, PROMPT, model="privacy-preview")
    assert status == 200
    assert "[PERSON_1]" in text and NAME not in text and EMAIL not in text
    assert mock_provider.received() == []  # preview never reaches a provider
