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


@pytest.mark.parametrize(
    "raw_path",
    [
        "/key/list/../../v1/models",
        "/credentials/x/../../v1/models",
        "/openai/v1/x/../../../v1/chat/completions",
        "/openai/v1/x/%2e%2e/%2E%2E/%2e%2e/v1/chat/completions",
        "/v1%2fchat%2fcompletions",
        "/admin/../v1/chat/completions",
    ],
)
def test_paths_that_only_normalise_to_an_allowed_route_are_refused(env, raw_path):
    """nginx matches locations on the normalised path; such requests must not
    reach LiteLLM with their raw path (which would hit other routes)."""
    import subprocess

    port = env.get("GATEWAY_PORT", "8080")
    out = subprocess.run(
        ["curl", "-s", "--path-as-is", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST",
         "-H", f"Authorization: Bearer {env['LITELLM_MASTER_KEY']}", "-H", "Content-Type: application/json",
         "-d", '{"model":"mock-gpt","messages":[{"role":"user","content":"x"}]}',
         f"http://127.0.0.1:{port}{raw_path}"],
        capture_output=True, text=True, timeout=30,
    )
    assert out.stdout == "400", (raw_path, out.stdout)


def test_canary_model_not_available_to_users(gateway, user_key):
    status, _, _ = chat(gateway, user_key, "hi", model="privacy-canary")
    assert status in (401, 403)


def test_preview_model_returns_masked_text(gateway, user_key, mock_provider):
    status, text, _ = chat(gateway, user_key, PROMPT, model="privacy-preview")
    assert status == 200
    assert "[PERSON_1]" in text and NAME not in text and EMAIL not in text
    assert mock_provider.received() == []  # preview never reaches a provider


def test_anthropic_code_path_masks_and_pseudonymises_metadata_user(gateway, master_key, mock_provider):
    """LiteLLM forwards metadata.user_id to Anthropic; it must arrive pseudonymised,
    and the Anthropic request format (separate system prompt) must be masked."""
    body = {
        "model": "mock-claude",
        "messages": [{"role": "system", "content": f"Ügyfél: {NAME}."}, {"role": "user", "content": PROMPT}],
        "metadata": {"user_id": "kovacs.peter.belso.azonosito"},
    }
    r = gateway.post("/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {master_key}"})
    assert r.status_code == 200, r.text
    assert NAME in r.json()["choices"][0]["message"]["content"]  # restored
    received = mock_provider.received()
    assert len(received) == 1
    sent = json.dumps(received[0], ensure_ascii=False)
    for value in (NAME, EMAIL, PHONE, "Kovács", "kovacs.peter.belso"):
        assert value not in sent, value
    assert received[0]["metadata"]["user_id"].startswith("u_")
