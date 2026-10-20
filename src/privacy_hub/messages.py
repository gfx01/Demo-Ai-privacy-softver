"""Locate every piece of free text in an OpenAI-style chat request/response.

Masking must cover *all* text that leaves the gateway, not just
``messages[-1].content``: system prompts, earlier turns, multi-part content and
tool-call arguments (JSON, possibly with ``\\u00e1`` escapes that a regex on
the raw string would miss) are all collected as ``TextSlot`` objects.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class UnsupportedContentError(Exception):
    """The request contains content the guardrail cannot inspect (images,
    audio, files, unknown parameters). Blocking is the fail-closed default."""


# OpenAI chat-completion parameters accepted from clients. Anything else is
# rejected: an unknown field may carry text that is never masked (e.g.
# ``prediction``, ``extra_body``, per-fallback ``messages`` overrides).
CLIENT_PARAMS = frozenset({
    "model", "messages", "stream", "stream_options", "temperature", "top_p", "n", "stop",
    "max_tokens", "max_completion_tokens", "presence_penalty", "frequency_penalty", "logit_bias",
    "logprobs", "top_logprobs", "seed", "user", "response_format", "tools", "tool_choice",
    "parallel_tool_calls", "reasoning_effort", "service_tier", "verbosity", "metadata",
})
# Keys LiteLLM 1.105.1 itself adds to the request before guardrails run.
LITELLM_INTERNAL_KEYS = frozenset({
    "proxy_server_request", "secret_fields", "litellm_call_id", "litellm_logging_obj",
    "litellm_metadata", "metadata", "user_api_key_dict",
})
MESSAGE_KEYS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id", "function_call", "cache_control"})


def check_request_params(data: dict, extra_allowed: frozenset[str] = frozenset()) -> None:
    """Reject unknown parameters that could carry text. Unknown keys holding a
    number, boolean, None or a non-JSON object (LiteLLM-internal state such as
    ``_litellm_strip_stream_usage``) cannot carry text and are let through."""
    known = CLIENT_PARAMS | LITELLM_INTERNAL_KEYS | extra_allowed
    unknown = sorted(k for k, v in data.items() if k not in known and isinstance(v, (str, list, tuple, dict)))
    if unknown:
        raise UnsupportedContentError(f"request parameter(s) not allowed through the privacy gateway: {', '.join(unknown)}")


@dataclass
class TextSlot:
    where: str
    get: Callable[[], str]
    set: Callable[[str], None]


def _dict_slot(obj: dict, key: str, where: str) -> TextSlot:
    def setter(value: str) -> None:
        obj[key] = value

    return TextSlot(where, lambda: obj[key], setter)


def _json_string_slots(holder: dict, key: str, where: str) -> list[TextSlot]:
    """Slots for every string inside a JSON document stored at holder[key].
    Falls back to treating the whole value as text if it is not valid JSON."""
    raw = holder.get(key)
    if not isinstance(raw, str) or not raw:
        return []
    try:
        doc = json.loads(raw)
    except ValueError:
        return [_dict_slot(holder, key, where)]

    state = {"doc": doc}
    slots: list[TextSlot] = []

    def walk(node: Any, path: list[Any]) -> None:
        if isinstance(node, str):
            slots.append(TextSlot(f"{where}{''.join(f'[{p!r}]' for p in path)}", make_get(path), make_set(path)))
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [i])

    def resolve(path: list[Any]) -> tuple[Any, Any]:
        parent: Any = None
        node: Any = state["doc"]
        for p in path:
            parent, node = node, node[p]
        return parent, node

    def make_get(path: list[Any]) -> Callable[[], str]:
        return lambda: resolve(path)[1]

    def make_set(path: list[Any]) -> Callable[[str], None]:
        def setter(value: str) -> None:
            if not path:
                state["doc"] = value
            else:
                parent, _ = resolve(path)
                parent[path[-1]] = value
            holder[key] = json.dumps(state["doc"], ensure_ascii=False)

        return setter

    walk(doc, [])
    return slots


def _tree_string_slots(node: Any, where: str, only_keys: frozenset[str] | None = None) -> list[TextSlot]:
    """Slots for string values inside a dict/list tree (edited in place).
    With ``only_keys``, only strings stored under those dict keys."""
    slots: list[TextSlot] = []

    def walk(obj: Any, path: str) -> None:
        items = obj.items() if isinstance(obj, dict) else enumerate(obj) if isinstance(obj, list) else ()
        for k, v in items:
            if isinstance(v, str):
                if only_keys is None or (isinstance(obj, dict) and k in only_keys):
                    slots.append(_dict_slot(obj, k, f"{path}[{k!r}]"))
            else:
                walk(v, f"{path}[{k!r}]")

    walk(node, where)
    return slots


def _arguments_slots(holder: dict, where: str) -> list[TextSlot]:
    if isinstance(holder.get("arguments"), (dict, list)):
        return _tree_string_slots(holder["arguments"], where)
    return _json_string_slots(holder, "arguments", where)


DESCRIPTION_KEYS = frozenset({"description", "title"})


def collect_definition_slots(data: dict) -> list[TextSlot]:
    """Free text in tool definitions and JSON-schema response formats
    (descriptions, titles). Names, enums and types are left alone: changing
    them would break the client's own tool handling."""
    slots: list[TextSlot] = []
    if data.get("tools") is not None:
        slots.extend(_tree_string_slots(data["tools"], "tools", DESCRIPTION_KEYS))
    rf = data.get("response_format")
    if isinstance(rf, dict) and rf.get("type") not in (None, "text", "json_object"):
        slots.extend(_tree_string_slots(rf, "response_format", DESCRIPTION_KEYS))
    return slots


def collect_request_slots(messages: list[dict], allow_non_text: bool = False) -> list[TextSlot]:
    if not isinstance(messages, list):
        raise UnsupportedContentError("messages must be a list")
    slots: list[TextSlot] = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise UnsupportedContentError(f"messages[{i}] is not an object")
        unknown = sorted(set(msg) - MESSAGE_KEYS)
        if unknown:
            raise UnsupportedContentError(f"messages[{i}] field(s) cannot be inspected: {', '.join(unknown)}")
        content = msg.get("content")
        if isinstance(content, str):
            slots.append(_dict_slot(msg, "content", f"messages[{i}].content"))
        elif isinstance(content, list):
            for j, part in enumerate(content):
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    slots.append(_dict_slot(part, "text", f"messages[{i}].content[{j}].text"))
                elif isinstance(part, str):
                    def setter(value: str, lst=content, idx=j) -> None:
                        lst[idx] = value

                    slots.append(TextSlot(f"messages[{i}].content[{j}]", lambda lst=content, idx=j: lst[idx], setter))
                elif not allow_non_text:
                    kind = part.get("type") if isinstance(part, dict) else type(part).__name__
                    raise UnsupportedContentError(f"content part of type {kind!r} cannot be inspected")
        elif content is not None:
            raise UnsupportedContentError(f"messages[{i}].content has unsupported type")
        for k, call in enumerate(msg.get("tool_calls") or []):
            fn = call.get("function") if isinstance(call, dict) else None
            if isinstance(fn, dict):
                slots.extend(_arguments_slots(fn, f"messages[{i}].tool_calls[{k}].function.arguments"))
        fn_call = msg.get("function_call")
        if isinstance(fn_call, dict):
            slots.extend(_arguments_slots(fn_call, f"messages[{i}].function_call.arguments"))
        # The optional participant name of user/assistant/system messages is
        # often a real name; tool/function messages use it for the function.
        if msg.get("name") and msg.get("role") in {"user", "assistant", "system", "developer"}:
            msg.pop("name", None)
    return slots


def restore_message_in_place(message: Any, restore: Callable[[str], str]) -> None:
    """Restore content and tool-call arguments of a response message object
    (pydantic model or dict)."""

    def get(obj: Any, name: str) -> Any:
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    def put(obj: Any, name: str, value: Any) -> None:
        if isinstance(obj, dict):
            obj[name] = value
        else:
            setattr(obj, name, value)

    content = get(message, "content")
    if isinstance(content, str):
        put(message, "content", restore(content))
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part["text"] = restore(part["text"])
    for call in get(message, "tool_calls") or []:
        fn = get(call, "function")
        if fn is not None and isinstance(get(fn, "arguments"), str):
            put(fn, "arguments", restore_json_text(get(fn, "arguments"), restore))
    fn_call = get(message, "function_call")
    if fn_call is not None and isinstance(get(fn_call, "arguments"), str):
        put(fn_call, "arguments", restore_json_text(get(fn_call, "arguments"), restore))


def restore_json_text(raw: str, restore: Callable[[str], str]) -> str:
    """Restore inside the string values of a JSON document so that restored
    values are correctly escaped. Non-JSON text is restored as plain text."""
    try:
        doc = json.loads(raw)
    except ValueError:
        return restore(raw)

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return restore(node)
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return json.dumps(walk(doc), ensure_ascii=False)
