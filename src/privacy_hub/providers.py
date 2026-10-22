"""In-process LiteLLM providers used by the gateway itself.

* ``canary``  – startup self-test target. It receives the request *after* the
  guardrail ran and reports whether any canary secret arrived in clear text.
  It also echoes the placeholder it saw, so the self-test can verify that
  restoration works (non-streaming and streaming).
* ``preview`` – shows the user exactly what would be sent to a provider: it
  echoes the (already masked) last user message. The guardrail skips
  restoration for this model.

Registered via ``litellm_settings.custom_provider_map`` in the LiteLLM config.
Neither provider makes network calls.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import litellm
from litellm import CustomLLM

# Synthetic canary values (pattern-detectable, so the check does not depend on
# NER quality). They are not real personal data.
CANARY_EMAIL = "canary.selftest@example.com"
CANARY_PHONE = "+36 30 999 0000"
CANARY_SECRETS = (CANARY_EMAIL, CANARY_PHONE, "canary.selftest", "999 0000")
CANARY_PROMPT = f"Self-test: contact {CANARY_EMAIL} or call {CANARY_PHONE}."


def _all_text(messages: list[dict]) -> str:
    parts: list[str] = []
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(p.get("text", "") for p in content if isinstance(p, dict))
    return "\n".join(parts)


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def canary_reply(messages: list[dict]) -> str:
    received = _all_text(messages)
    leaked = sum(1 for secret in CANARY_SECRETS if secret in received)
    # Echo what arrived in place of the email so the client can check restoration.
    return f"CANARY leaked={leaked} echo={_last_user_text(messages)}"


def _response(model: str, text: str) -> litellm.ModelResponse:
    return litellm.ModelResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=model,
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )


async def _stream(text: str, size: int = 7) -> AsyncIterator[dict[str, Any]]:
    # Small, uneven chunks on purpose: placeholders get split across chunks.
    for i in range(0, len(text), size):
        yield {"text": text[i : i + size], "is_finished": False, "finish_reason": "", "usage": None, "index": 0}
    yield {"text": "", "is_finished": True, "finish_reason": "stop", "usage": None, "index": 0}


class _EchoProvider(CustomLLM):
    def reply(self, messages: list[dict]) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def completion(self, model, messages, *args, **kwargs):  # type: ignore[override]
        return _response(model, self.reply(messages))

    async def acompletion(self, model, messages, *args, **kwargs):  # type: ignore[override]
        return _response(model, self.reply(messages))

    async def astreaming(self, model, messages, *args, **kwargs):  # type: ignore[override]
        async for chunk in _stream(self.reply(messages)):
            yield chunk


class CanaryProvider(_EchoProvider):
    def reply(self, messages):
        return canary_reply(messages)


class PreviewProvider(_EchoProvider):
    def reply(self, messages):
        text = _last_user_text(messages)
        return "🔒 This is exactly what would be sent to the model:\n\n" + text


canary = CanaryProvider()
preview = PreviewProvider()
