"""Mock OpenAI-compatible LLM provider (no API keys needed).

Models (``model`` field):
  mock-echo     echoes the last user message
  mock-inflect  echoes, then re-uses every bracket token it saw with a
                Hungarian dative ending ("[PERSON_1]-nak"), like a real model
                that inflects placeholders
  mock-error    always fails with HTTP 500 (provider-error path)

``POST /v1/messages`` is a minimal, non-streaming Anthropic Messages API
endpoint (echo) so that LiteLLM's anthropic/* code path can be tested too.

Every received request body is kept in a small in-memory ring buffer so
integration tests can prove what a provider would have seen
(``GET /_mock/requests``, bearer token ``MOCK_LLM_INSPECT_TOKEN``). This is a
test fixture: never point it at real data.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import time
import uuid
from collections import deque
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="mock-llm", docs_url=None, redoc_url=None)
RECEIVED: deque[dict[str, Any]] = deque(maxlen=200)
TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")
CHUNK = int(os.environ.get("MOCK_LLM_CHUNK_CHARS", "3"))


def _check(authorization: str | None) -> None:
    expected = os.environ.get("MOCK_LLM_INSPECT_TOKEN", "")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid token")


def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return c or ""
    return ""


def _reply(model: str, messages: list[dict]) -> str:
    text = _last_user(messages)
    if "inflect" in model:
        tokens = list(dict.fromkeys(TOKEN_RE.findall(text)))
        extra = " ".join(f"Írtam {t}-nak." for t in tokens)
        return f"Mock válasz: {text}\n{extra}".strip()
    return f"Mock reply: {text}"


def _usage(messages: list[dict], reply: str) -> dict[str, int]:
    prompt = sum(len(str(m.get("content", ""))) for m in messages) // 4 + 1
    completion = len(reply) // 4 + 1
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


@app.get("/v1/models")
def models() -> dict:
    return {"object": "list", "data": [{"id": m, "object": "model"} for m in ("mock-echo", "mock-inflect", "mock-error")]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    RECEIVED.append({"received_at": time.time(), "body": body})
    model = body.get("model", "mock-echo")
    if "error" in model:
        return JSONResponse({"error": {"message": "mock provider failure", "type": "server_error"}}, status_code=500)
    messages = body.get("messages") or []
    reply = _reply(model, messages)
    cid = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    if not body.get("stream"):
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": reply}}],
            "usage": _usage(messages, reply),
        }

    async def events():
        def chunk(delta: dict, finish: str | None = None) -> str:
            data = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        yield chunk({"role": "assistant", "content": ""})
        for i in range(0, len(reply), CHUNK):
            yield chunk({"content": reply[i : i + CHUNK]})
            await asyncio.sleep(0)
        yield chunk({}, "stop")
        if (body.get("stream_options") or {}).get("include_usage"):
            usage = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [], "usage": _usage(messages, reply)}
            yield f"data: {json.dumps(usage)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    body = await request.json()
    RECEIVED.append({"received_at": time.time(), "body": body, "api": "anthropic"})
    messages = []
    for m in body.get("messages") or []:
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        messages.append({"role": m.get("role"), "content": content})
    reply = _reply("mock-echo", messages)
    usage = _usage(messages, reply)
    return {
        "id": f"msg_{uuid.uuid4().hex[:16]}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", "mock-claude"),
        "content": [{"type": "text", "text": reply}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"]},
    }


@app.get("/_mock/requests")
def received(authorization: Annotated[str | None, Header()] = None) -> list[dict]:
    _check(authorization)
    return list(RECEIVED)


@app.delete("/_mock/requests")
def clear(authorization: Annotated[str | None, Header()] = None) -> dict:
    _check(authorization)
    RECEIVED.clear()
    return {"cleared": True}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
