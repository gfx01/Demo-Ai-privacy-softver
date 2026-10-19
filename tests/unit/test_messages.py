import json

import pytest

from privacy_hub.messages import (
    UnsupportedContentError,
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
