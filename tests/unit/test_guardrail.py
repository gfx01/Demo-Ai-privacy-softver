"""Guardrail behaviour against the real LiteLLM base classes (no network)."""

import base64
import json
import os

import httpx
import pytest

litellm = pytest.importorskip("litellm")
pytestmark = pytest.mark.gateway

from fastapi import HTTPException  # noqa: E402
from litellm.types.guardrails import GuardrailEventHooks  # noqa: E402
from litellm.types.utils import Delta, ModelResponse, ModelResponseStream, StreamingChoices  # noqa: E402

from privacy_hub.analyzer_client import AnalyzerClient  # noqa: E402
from privacy_hub.audit import AuditUnavailableError, MemoryAuditSink  # noqa: E402
from privacy_hub.config import FatalConfigError  # noqa: E402
from privacy_hub.guardrail import REQUEST_ID_KEY, PrivacyHubGuardrail  # noqa: E402
from privacy_hub.masking import MaskingSession, Span  # noqa: E402
from privacy_hub.org_settings import OrgSettings  # noqa: E402
from privacy_hub.store import InMemoryMappingStore  # noqa: E402

NAME = "John Smith"
EMAIL = "john.smith@example.com"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PRIVACY_HUB_MAPPING_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("PRIVACY_HUB_ANALYZER_TOKEN", "t" * 32)
    monkeypatch.setenv("PRIVACY_HUB_STORE_URL", "memory://")
    monkeypatch.setenv("PRIVACY_HUB_AUDIT_REQUIRED", "false")
    monkeypatch.setenv("PRIVACY_HUB_DEFAULT_LANGUAGE", "en")
    monkeypatch.delenv("PRIVACY_HUB_DATABASE_URL", raising=False)


def fake_mask_handler(request: httpx.Request) -> httpx.Response:
    """Stands in for the analyzer: masks NAME and EMAIL with the real session."""
    body = json.loads(request.content)
    session = MaskingSession(body["strategy"], existing_texts=body["texts"], language=body["language"])
    masked = []
    for t in body["texts"]:
        spans = []
        for value, etype in ((NAME, "PERSON"), (EMAIL, "EMAIL_ADDRESS")):
            start = t.find(value)
            while start != -1:
                spans.append(Span(etype, start, start + len(value)))
                start = t.find(value, start + 1)
        masked.append(session.apply(t, spans))
    r = session.result(masked)
    return httpx.Response(
        200,
        json={"masked_texts": r.masked_texts, "mapping": r.mapping.to_dict(), "entity_counts": r.entity_counts, "language": "en"},
    )


def make_guardrail(env, handler=fake_mask_handler, mode="mask"):
    g = PrivacyHubGuardrail(guardrail_name="privacy-hub", event_hook=["pre_call", "post_call"], default_on=True)
    g.analyzer = AnalyzerClient("http://analyzer", "t" * 32, 2.0, transport=httpx.MockTransport(handler))
    g.audit = MemoryAuditSink()

    async def settings(org_id):
        return OrgSettings(org_id, mode, "bracket", "en")

    g.settings.get = settings
    return g


class UAK:
    user_id = "u1"
    team_id = "t1"
    key_alias = "k1"
    end_user_id = None
    metadata = {"organisation_id": "acme"}
    team_metadata = {}


def request(content=None):
    return {
        "model": "mock-gpt",
        "messages": [
            {"role": "system", "content": f"Assistant for {NAME}."},
            {"role": "user", "content": content or f"Write to {EMAIL} on behalf of {NAME}."},
        ],
        "user": "alice@corp.example",
        "metadata": {},
    }


def test_hooks_are_defined_on_the_leaf_class():
    # LiteLLM detects overrides via type(cb).__dict__ - inheritance is not enough.
    for hook in ("async_pre_call_hook", "async_post_call_success_hook", "async_post_call_streaming_iterator_hook"):
        assert hook in PrivacyHubGuardrail.__dict__
    assert "apply_guardrail" not in PrivacyHubGuardrail.__dict__  # keep native hook path


def test_constructor_refuses_unsafe_config(env, monkeypatch):
    with pytest.raises(FatalConfigError):
        PrivacyHubGuardrail(guardrail_name="x", event_hook=["pre_call", "post_call"], default_on=False)
    monkeypatch.delenv("PRIVACY_HUB_MAPPING_KEY")
    with pytest.raises(FatalConfigError) as exc:
        PrivacyHubGuardrail(guardrail_name="x", event_hook=["pre_call", "post_call"], default_on=True)
    # LiteLLM swallows ValueError/TypeError from guardrail constructors.
    assert not isinstance(exc.value, (ValueError, TypeError))


def test_cannot_be_disabled_by_metadata_or_request(env):
    g = make_guardrail(env)
    data = {
        "metadata": {
            "user_api_key_metadata": {"disable_global_guardrails": True, "opted_out_global_guardrails": ["privacy-hub"]},
            "guardrails": [],
        },
        "guardrails": ["something-else"],
    }
    assert g.should_run_guardrail(data, GuardrailEventHooks.pre_call)
    assert g.should_run_guardrail(data, GuardrailEventHooks.post_call)
    assert not g.should_run_guardrail(data, GuardrailEventHooks.during_call)


async def test_mask_then_restore_roundtrip(env):
    g = make_guardrail(env)
    data = request()
    data["metadata"][REQUEST_ID_KEY] = "attacker-chosen-id"
    data["metadata"]["user_id"] = "Kovacs Peter"  # LiteLLM forwards this to Anthropic
    out = await g.async_pre_call_hook(UAK(), None, data, "acompletion")
    sent = json.dumps(out["messages"])
    assert NAME not in sent and EMAIL not in sent
    assert "[PERSON_1]" in sent and "[EMAIL_1]" in sent
    assert out["user"].startswith("u_") and "alice" not in out["user"]
    assert out["metadata"]["user_id"].startswith("u_") and "Kovacs" not in out["metadata"]["user_id"]
    request_id = out["metadata"][REQUEST_ID_KEY]
    assert request_id != "attacker-chosen-id"
    # the store holds ciphertext only
    assert all(NAME.encode() not in blob for blob in g.store.raw_items().values())

    response = ModelResponse(choices=[{"index": 0, "message": {"role": "assistant", "content": "Done for [PERSON_1] ([EMAIL_1])."}}])
    response = await g.async_post_call_success_hook(out, UAK(), response)
    assert response.choices[0].message.content == f"Done for {NAME} ({EMAIL})."
    assert await g.store.get(request_id, binding="acme") is None  # deleted after response
    outcomes = [(e.event, e.outcome) for e in g.audit.events]
    assert outcomes == [("request", "forwarded"), ("response", "completed")]
    assert g.audit.events[0].entity_counts == {"PERSON": 2, "EMAIL_ADDRESS": 1}


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")),
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
        lambda r: httpx.Response(500, json={"detail": "boom"}),
        lambda r: httpx.Response(200, json={"masked_texts": ["only one"], "mapping": {"version": 1, "strategy": "bracket", "entries": []}}),
        lambda r: httpx.Response(200, text="not json"),
    ],
    ids=["connect-error", "timeout", "http-500", "wrong-length", "malformed"],
)
async def test_fail_closed_when_analyzer_fails(env, handler):
    g = make_guardrail(env, handler=handler)
    data = request()
    original = json.dumps(data["messages"])
    with pytest.raises(HTTPException) as exc:
        await g.async_pre_call_hook(UAK(), None, data, "acompletion")
    assert exc.value.status_code == 503
    assert json.dumps(data["messages"]) == original  # nothing half-masked
    assert g.audit.events[-1].outcome == "blocked_error"


async def test_strict_mode_blocks_and_does_not_echo_values(env):
    g = make_guardrail(env, mode="strict")
    with pytest.raises(HTTPException) as exc:
        await g.async_pre_call_hook(UAK(), None, request(), "acompletion")
    assert exc.value.status_code == 400
    detail = json.dumps(exc.value.detail)
    assert "PERSON" in detail and NAME not in detail and EMAIL not in detail
    assert g.audit.events[-1].outcome == "blocked_strict"


async def test_strict_mode_allows_clean_requests(env):
    g = make_guardrail(env, mode="strict")
    data = {"model": "mock-gpt", "messages": [{"role": "user", "content": "What is the capital of Hungary?"}]}
    out = await g.async_pre_call_hook(UAK(), None, data, "acompletion")
    assert out["messages"][0]["content"] == "What is the capital of Hungary?"


@pytest.mark.parametrize("call_type", ["aembedding", "aimage_generation", "anthropic_messages", "pass_through_endpoint", "atranscription"])
async def test_other_call_types_are_rejected(env, call_type):
    g = make_guardrail(env)
    with pytest.raises(HTTPException) as exc:
        await g.async_pre_call_hook(UAK(), None, request(), call_type)
    assert exc.value.status_code == 403


async def test_images_are_rejected(env):
    g = make_guardrail(env)
    data = {"model": "m", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]}
    with pytest.raises(HTTPException) as exc:
        await g.async_pre_call_hook(UAK(), None, data, "acompletion")
    assert exc.value.status_code == 400


async def test_audit_failure_blocks_when_required(env, monkeypatch):
    g = make_guardrail(env)
    object.__setattr__(g.cfg, "audit_required", True)

    class Broken(MemoryAuditSink):
        async def record(self, event):
            raise AuditUnavailableError("down")

    g.audit = Broken()
    with pytest.raises(HTTPException) as exc:
        await g.async_pre_call_hook(UAK(), None, request(), "acompletion")
    assert exc.value.status_code == 503


async def test_streaming_restore_with_split_tokens(env):
    g = make_guardrail(env)
    data = await g.async_pre_call_hook(UAK(), None, request(), "acompletion")
    reply = "Sent to [EMAIL_1] for [PERSON_1]. Regards, [PERSON_1]"

    async def upstream():
        for i in range(0, len(reply), 2):
            yield ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content=reply[i : i + 2]))])
        yield ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content=""), finish_reason="stop")])

    chunks = [c async for c in g.async_post_call_streaming_iterator_hook(UAK(), upstream(), data)]
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == f"Sent to {EMAIL} for {NAME}. Regards, {NAME}"
    assert g.audit.events[-1].outcome == "completed"


async def test_streaming_without_finish_reason_flushes_tail(env):
    g = make_guardrail(env)
    data = await g.async_pre_call_hook(UAK(), None, request(), "acompletion")

    async def upstream():
        yield ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content="Bye [PERSON_1]"))])

    chunks = [c async for c in g.async_post_call_streaming_iterator_hook(UAK(), upstream(), data)]
    assert "".join(c.choices[0].delta.content or "" for c in chunks) == f"Bye {NAME}"


async def test_preview_model_is_not_restored(env):
    g = make_guardrail(env)
    data = request()
    data["model"] = g.cfg.preview_model
    out = await g.async_pre_call_hook(UAK(), None, data, "acompletion")
    response = ModelResponse(choices=[{"index": 0, "message": {"role": "assistant", "content": "[PERSON_1]"}}])
    response = await g.async_post_call_success_hook(out, UAK(), response)
    assert response.choices[0].message.content == "[PERSON_1]"


async def test_mapping_of_other_org_cannot_be_used(env):
    g = make_guardrail(env)
    out = await g.async_pre_call_hook(UAK(), None, request(), "acompletion")

    class Other(UAK):
        metadata = {"organisation_id": "other-org"}

    response = ModelResponse(choices=[{"index": 0, "message": {"role": "assistant", "content": "[PERSON_1]"}}])
    response = await g.async_post_call_success_hook(out, Other(), response)
    assert response.choices[0].message.content == "[PERSON_1]"  # not restored with acme's mapping


async def test_streaming_tool_call_arguments_are_restored(env):
    from litellm.types.utils import ChatCompletionDeltaToolCall, Function

    g = make_guardrail(env)
    data = await g.async_pre_call_hook(UAK(), None, request(), "acompletion")
    args = '{"to": "[EMAIL_1]", "name": "[PERSON_1]"}'

    async def upstream():
        yield ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content=None, tool_calls=[
            ChatCompletionDeltaToolCall(id="call_1", index=0, type="function", function=Function(name="send_mail", arguments=""))]))])
        for i in range(0, len(args), 3):
            yield ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content=None, tool_calls=[
                ChatCompletionDeltaToolCall(index=0, function=Function(arguments=args[i : i + 3]))]))])
        yield ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content=None), finish_reason="tool_calls")])

    chunks = [c async for c in g.async_post_call_streaming_iterator_hook(UAK(), upstream(), data)]
    joined = "".join(
        (tc.function.arguments or "") for c in chunks for ch in c.choices for tc in (ch.delta.tool_calls or [])
    )
    assert json.loads(joined) == {"to": EMAIL, "name": NAME}
    names = [tc.function.name for c in chunks for ch in c.choices for tc in (ch.delta.tool_calls or []) if tc.function.name]
    assert names == ["send_mail"]
