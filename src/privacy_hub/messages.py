"""Locate every piece of free text in an OpenAI-style chat request/response.

Masking must cover *all* text that leaves the gateway, not just
``messages[-1].content``: system prompts, earlier turns, multi-part content and
tool-call arguments (JSON, possibly with ``\\u00e1`` escapes that a regex on
the raw string would miss) are all collected as ``TextSlot`` objects.
"""

from __future__ import annotations

import json
import re
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
ROLES = frozenset({"system", "developer", "user", "assistant", "tool", "function"})
# Nested objects are closed too: an unknown key inside a content part or a
# tool call would be forwarded without being masked.
TEXT_PART_KEYS = frozenset({"type", "text", "cache_control"})
TOOL_CALL_KEYS = frozenset({"id", "type", "function", "index"})
FUNCTION_KEYS = frozenset({"name", "arguments"})
CACHE_CONTROL_KEYS = frozenset({"type", "ttl"})
# Identifiers and function names are sent as they are, so only plain tokens
# are accepted (provider ids such as call_abc123 / toolu_01...).
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

# Request parameters whose value could carry text are checked for shape.
ENUM_PARAMS = {
    "reasoning_effort": frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "default", "disable"}),
    "verbosity": frozenset({"low", "medium", "high"}),
    "service_tier": frozenset({"auto", "default", "flex", "scale", "priority"}),
}
NUMBER_PARAMS = frozenset({
    "temperature", "top_p", "n", "max_tokens", "max_completion_tokens", "presence_penalty",
    "frequency_penalty", "seed", "top_logprobs",
})
BOOL_PARAMS = frozenset({"stream", "logprobs", "parallel_tool_calls"})
STREAM_OPTION_KEYS = frozenset({"include_usage", "include_obfuscation", "continuous_usage_stats"})
TOOL_CHOICE_STRINGS = frozenset({"auto", "none", "required", "any"})


def check_request_params(data: dict, extra_allowed: frozenset[str] = frozenset()) -> None:
    """Reject unknown parameters that could carry text. Unknown keys holding a
    number, boolean, None or a non-JSON object (LiteLLM-internal state such as
    ``_litellm_strip_stream_usage``) cannot carry text and are let through."""
    known = CLIENT_PARAMS | LITELLM_INTERNAL_KEYS | extra_allowed
    unknown = sorted(k for k, v in data.items() if k not in known and isinstance(v, (str, list, tuple, dict)))
    if unknown:
        raise UnsupportedContentError(f"request parameter(s) not allowed through the privacy gateway: {', '.join(unknown)}")
    _check_param_values(data, extra_allowed)


def _bad(param: str) -> UnsupportedContentError:
    return UnsupportedContentError(f"request parameter {param!r} has a value the privacy gateway cannot inspect")


def _check_param_values(data: dict, extra_allowed: frozenset[str]) -> None:
    """Parameters that are not masked must not be able to carry free text."""
    for key, value in data.items():
        if key in extra_allowed or value is None:
            continue
        if key in ENUM_PARAMS and value not in ENUM_PARAMS[key]:
            raise _bad(key)
        if key in NUMBER_PARAMS and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise _bad(key)
        if key in BOOL_PARAMS and not isinstance(value, bool):
            raise _bad(key)
        if key == "model" and not isinstance(value, str):
            raise _bad(key)
        if key == "stream_options" and not (
            isinstance(value, dict) and set(value) <= STREAM_OPTION_KEYS and all(isinstance(v, bool) for v in value.values())
        ):
            raise _bad(key)
        if key == "logit_bias" and not (
            isinstance(value, dict)
            and all(str(k).isdigit() and isinstance(v, (int, float)) and not isinstance(v, bool) for k, v in value.items())
        ):
            raise _bad(key)
        if key == "tool_choice" and not _valid_tool_choice(value):
            raise _bad(key)


def _valid_tool_choice(value: object) -> bool:
    if isinstance(value, str):
        return value in TOOL_CHOICE_STRINGS
    if not isinstance(value, dict) or not set(value) <= {"type", "function", "name", "disable_parallel_tool_use"}:
        return False
    if value.get("type") not in (None, "function", "auto", "any", "tool", "none", "required"):
        return False
    fn = value.get("function")
    if fn is not None and not (isinstance(fn, dict) and set(fn) <= {"name"} and _identifier(fn.get("name"))):
        return False
    return value.get("name") is None or _identifier(value.get("name"))


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(IDENTIFIER.match(value))


def _check_cache_control(value: object, where: str) -> None:
    if value is None:
        return
    if not (
        isinstance(value, dict)
        and set(value) <= CACHE_CONTROL_KEYS
        and value.get("type") == "ephemeral"
        and value.get("ttl") in (None, "5m", "1h")
    ):
        raise UnsupportedContentError(f"{where}.cache_control cannot be inspected")


def _check_message_structure(msg: dict, i: int) -> None:
    """Closed schema for the parts of a message that are not masked."""
    if msg.get("role") not in ROLES:
        raise UnsupportedContentError(f"messages[{i}].role is not supported")
    _check_cache_control(msg.get("cache_control"), f"messages[{i}]")
    if "tool_call_id" in msg and msg["tool_call_id"] is not None and not _identifier(msg["tool_call_id"]):
        raise UnsupportedContentError(f"messages[{i}].tool_call_id must be a plain identifier")
    if msg.get("role") in ("tool", "function") and msg.get("name") is not None and not _identifier(msg["name"]):
        raise UnsupportedContentError(f"messages[{i}].name must be a plain identifier")
    calls = msg.get("tool_calls")
    if calls is not None:
        if not isinstance(calls, list):
            raise UnsupportedContentError(f"messages[{i}].tool_calls must be a list")
        for k, call in enumerate(calls):
            where = f"messages[{i}].tool_calls[{k}]"
            if not isinstance(call, dict) or not set(call) <= TOOL_CALL_KEYS:
                raise UnsupportedContentError(f"{where} has fields that cannot be inspected")
            if call.get("type") not in (None, "function") or (call.get("id") is not None and not _identifier(call["id"])):
                raise UnsupportedContentError(f"{where} has an unsupported type or id")
            _check_function(call.get("function"), where + ".function", required=True)
    if msg.get("function_call") is not None:
        _check_function(msg["function_call"], f"messages[{i}].function_call", required=True)


def _check_function(fn: object, where: str, required: bool) -> None:
    if fn is None and not required:
        return
    if not isinstance(fn, dict) or not set(fn) <= FUNCTION_KEYS or not _identifier(fn.get("name")):
        raise UnsupportedContentError(f"{where} must be an object with a plain name and arguments")
    if fn.get("arguments") is not None and not isinstance(fn["arguments"], (str, dict, list)):
        raise UnsupportedContentError(f"{where}.arguments cannot be inspected")


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
        _check_message_structure(msg, i)
        content = msg.get("content")
        if isinstance(content, str):
            slots.append(_dict_slot(msg, "content", f"messages[{i}].content"))
        elif isinstance(content, list):
            for j, part in enumerate(content):
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    extra = sorted(set(part) - TEXT_PART_KEYS)
                    if extra:
                        raise UnsupportedContentError(f"messages[{i}].content[{j}] field(s) cannot be inspected: {', '.join(extra)}")
                    _check_cache_control(part.get("cache_control"), f"messages[{i}].content[{j}]")
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
