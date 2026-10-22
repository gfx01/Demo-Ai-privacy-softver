import json

import pytest

from privacy_hub.messages import (
    UnsupportedContentError,
    check_request_params,
    collect_request_slots,
    restore_json_text,
    restore_message_in_place,
)


def test_collects_all_text_locations_and_drops_names():
    messages = [
        {"role": "system", "content": "You help Kovács Péter."},
        {"role": "user", "name": "Kovacs_Peter", "content": [{"type": "text", "text": "Hi"}]},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": json.dumps({"to": "Kovács", "n": [1, "x"]})}}],
        },
        {"role": "tool", "name": "f", "tool_call_id": "1", "content": "done"},
    ]
    slots = collect_request_slots(messages)
    texts = [s.get() for s in slots]
    assert texts == ["You help Kovács Péter.", "Hi", "Kovács", "x", "done"]
    assert "name" not in messages[1]  # participant name could be a real name
    assert messages[3]["name"] == "f"  # function name kept for tool messages
    for s in slots:
        s.set(s.get().upper())
    args = json.loads(messages[2]["tool_calls"][0]["function"]["arguments"])
    assert args == {"to": "KOVÁCS", "n": [1, "X"]}


def test_non_text_content_is_blocked_by_default():
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:..."}}]}]
    with pytest.raises(UnsupportedContentError):
        collect_request_slots(messages)
    assert collect_request_slots(messages, allow_non_text=True) == []


def test_non_json_tool_arguments_are_treated_as_text():
    messages = [{"role": "assistant", "tool_calls": [{"function": {"name": "f", "arguments": "not json Kovács"}}]}]
    slots = collect_request_slots(messages)
    assert [s.get() for s in slots] == ["not json Kovács"]


def test_restore_message_in_place_handles_dicts_and_json_escaping():
    msg = {"content": "Hello X", "tool_calls": [{"function": {"arguments": '{"name": "X"}'}}]}
    restore_message_in_place(msg, lambda s: s.replace("X", 'Kovács "Peti"'))
    assert msg["content"] == 'Hello Kovács "Peti"'
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"name": 'Kovács "Peti"'}


def test_restore_json_text_falls_back_to_plain_text():
    assert restore_json_text("plain X", lambda s: s.replace("X", "Y")) == "plain Y"


def test_request_parameter_allowlist():
    from privacy_hub.messages import check_request_params

    ok = {"model": "m", "messages": [], "stream": True, "temperature": 0.2, "metadata": {}, "_litellm_flag": True,
          "litellm_logging_obj": object()}
    check_request_params(ok)
    for bad in ({"prediction": {"type": "content", "content": "Kovács"}}, {"fallbacks": [{"model": "x", "messages": []}]},
                {"extra_body": {"x": "y"}}, {"prompt": "Kovács"}):
        with pytest.raises(UnsupportedContentError):
            check_request_params({**ok, **bad})
    check_request_params({**ok, "custom_flag": "x"}, extra_allowed=frozenset({"custom_flag"}))


def test_unknown_message_fields_are_rejected_and_dict_arguments_masked():
    with pytest.raises(UnsupportedContentError):
        collect_request_slots([{"role": "assistant", "content": "x", "reasoning_content": "Kovács"}])
    messages = [{"role": "assistant", "tool_calls": [{"function": {"name": "f", "arguments": {"to": "Kovács"}}}]}]
    slots = collect_request_slots(messages)
    assert [s.get() for s in slots] == ["Kovács"]
    slots[0].set("[PERSON_1]")
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == {"to": "[PERSON_1]"}


def test_tool_and_schema_descriptions_are_collected():
    from privacy_hub.messages import collect_definition_slots

    data = {
        "tools": [{"type": "function", "function": {"name": "mail", "description": "Mail Kovács Péter",
                   "parameters": {"type": "object", "properties": {"to": {"type": "string", "enum": ["a"], "description": "x"}}}}}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "s", "schema": {"title": "T", "type": "object"}}},
    }
    assert sorted(s.get() for s in collect_definition_slots(data)) == ["Mail Kovács Péter", "T", "x"]


# -- closed schema for nested fields that are not masked ------------------------------
@pytest.mark.parametrize(
    "message",
    [
        {"role": "user", "content": [{"type": "text", "text": "x", "note": "Kovács János"}]},
        {"role": "user", "content": [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral", "x": "Kovács"}}]},
        {"role": "user", "content": "x", "cache_control": {"type": "Kovács János"}},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}, "x": "Kovács"}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}", "y": "Kovács"}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": "Kovács János"}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "Kovács János", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "assistant", "content": None, "tool_calls": "Kovács János"},
        {"role": "assistant", "content": None, "function_call": "Kovács János"},
        {"role": "tool", "content": "x", "tool_call_id": "Kovács János"},
        {"role": "tool", "content": "x", "tool_call_id": "c1", "name": "Kovács János"},
        {"role": "Kovács János", "content": "x"},
    ],
)
def test_nested_fields_that_could_carry_text_are_refused(message):
    with pytest.raises(UnsupportedContentError):
        collect_request_slots([message])


def test_well_formed_tool_conversation_passes():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Hívd fel Kovács Pétert", "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_abc123", "type": "function", "function": {"name": "phone.call", "arguments": '{"to": "Kovács Péter"}'}}]},
        {"role": "tool", "tool_call_id": "call_abc123", "name": "phone.call", "content": "ok"},
    ]
    slots = collect_request_slots(messages)
    assert [s.where for s in slots] == [
        "messages[0].content[0].text", "messages[1].tool_calls[0].function.arguments['to']", "messages[2].content",
    ]


@pytest.mark.parametrize(
    "param, value, ok",
    [
        ("reasoning_effort", "high", True),
        ("reasoning_effort", "Kovács János", False),
        ("verbosity", "Kovács", False),
        ("service_tier", "auto", True),
        ("temperature", 0.2, True),
        ("temperature", "Kovács", False),
        ("seed", True, False),
        ("stream", "yes", False),
        ("stream_options", {"include_usage": True}, True),
        ("stream_options", {"include_usage": True, "note": "Kovács"}, False),
        ("logit_bias", {"1234": -100}, True),
        ("logit_bias", {"Kovács": 1}, False),
        ("tool_choice", "auto", True),
        ("tool_choice", {"type": "function", "function": {"name": "lookup"}}, True),
        ("tool_choice", {"type": "function", "function": {"name": "Kovács János"}}, False),
        ("tool_choice", "Kovács János", False),
    ],
)
def test_parameter_values_are_checked(param, value, ok):
    data = {"model": "m", "messages": [], param: value}
    if ok:
        check_request_params(data)
    else:
        with pytest.raises(UnsupportedContentError):
            check_request_params(data)
