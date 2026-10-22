"""Audit log without raw PII, budget enforcement and the admin portal,
against the running stack."""

from __future__ import annotations

import json
import random
import re
import subprocess
import time
import uuid

import pytest

from privacy_hub import hu_ids

from .conftest import ROOT, chat, compose, litellm_api

pytestmark = pytest.mark.integration


def _unique_pii() -> dict[str, str]:
    rng = random.Random()
    return {
        "email": f"teszt.{uuid.uuid4().hex[:10]}@example.com",
        "phone": f"+36 30 000 {rng.randrange(1000, 9999)}",
        "taj": hu_ids.gen_taj(rng),
    }


# --- audit log / storage -----------------------------------------------------------
def test_no_raw_pii_in_database_store_or_logs(gateway, user_key, mock_provider):
    pii = _unique_pii()
    prompt = (f"Kérlek, írj Kovács Péternek a {pii['email']} címre, telefon: {pii['phone']}, "
              f"TAJ-szám: {pii['taj']}.")
    for stream in (False, True):
        status, text, body = chat(gateway, user_key, prompt, stream=stream)
        assert status == 200, body
        assert pii["email"] in text and pii["taj"] in text  # restored for the user
    for request in mock_provider.received():
        sent = json.dumps(request, ensure_ascii=False)
        for value in pii.values():
            assert value not in sent

    time.sleep(12)  # LiteLLM writes spend logs in batches
    dump = compose("exec", "-T", "postgres", "pg_dumpall", "-U", "postgres").stdout
    keys = compose("exec", "-T", "redis", "sh", "-c",
                   'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan').stdout.split()
    values = ""
    for k in keys:  # values are binary (encrypted): decode leniently, plaintext would still show
        raw = subprocess.run(["docker", "compose", "exec", "-T", "redis", "sh", "-c",
                              f'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --raw GET {k}'],
                             cwd=ROOT, capture_output=True, timeout=60).stdout
        values += raw.decode("utf-8", errors="replace")
    logs = compose("logs", "--no-color", timeout=120).stdout
    # the searches below must not pass vacuously
    assert "audit_events" in dump and "LiteLLM_SpendLogs" in dump and "privacy self-test passed" in logs
    digits = {k: re.sub(r"\D", "", v) for k, v in pii.items() if k != "email"}
    for where, blob in (("postgres", dump), ("redis", values), ("container logs", logs)):
        for name, value in pii.items():
            assert value not in blob, f"{name} found in {where}"
        compact = re.sub(r"\D", "", blob)
        for name, value in digits.items():
            assert value not in compact or len(value) < 9, f"{name} digits found in {where}"

    # ...but the audit trail recorded the requests, with types and counts only.
    rows = compose("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "privacyhub", "-At", "-c",
                   "SELECT outcome, entity_counts::text FROM audit_events ORDER BY id DESC LIMIT 4").stdout
    assert "completed" in rows and "EMAIL_ADDRESS" in rows and "HU_TAJ" in rows


# --- budgets -----------------------------------------------------------------------
def _blocked_by_budget(gateway, key: str, timeout_s: float = 40) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status, _, body = chat(gateway, key, "Szia, mi a helyzet?")
        if status != 200:
            return {"status": status, "body": body}
        time.sleep(2)
    raise AssertionError("budget was never enforced")


def test_key_budget_is_enforced(gateway, mock_provider):
    alias = f"budget-test-{uuid.uuid4().hex[:8]}"
    key = litellm_api("POST", "/key/generate", {"models": ["mock-gpt"], "max_budget": 0.0000001, "key_alias": alias,
                                                "metadata": {"organisation_id": "default"}})["key"]
    try:
        status, _, body = chat(gateway, key, "Szia, mi a helyzet?")
        assert status == 200, body
        blocked = _blocked_by_budget(gateway, key)
        assert "budget" in json.dumps(blocked["body"]).lower(), blocked
        mock_provider.clear()
        status, _, _ = chat(gateway, key, "Még egy kérés.")
        assert status != 200 and mock_provider.received() == []
    finally:
        litellm_api("POST", "/key/delete", {"keys": [key]})


# --- admin portal ------------------------------------------------------------------
def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_admin_requires_authentication(gateway, admin_auth):
    assert gateway.get("/admin").status_code == 401
    assert gateway.get("/admin", auth=(admin_auth[0], "wrong-password-123")).status_code == 401
    for path in ("/admin", "/admin/settings", "/admin/budgets", "/admin/audit"):
        r = gateway.get(path, auth=admin_auth)
        assert r.status_code == 200, path
        assert "default-src 'self'" in r.headers["content-security-policy"]


def test_admin_forms_require_csrf(gateway, admin_auth):
    r = gateway.post("/admin/settings/default", auth=admin_auth,
                     data={"mode": "strict", "strategy": "bracket", "language": "hu"})
    assert r.status_code == 403


def _org_form(html: str, org: str) -> dict:
    block = html.split(f'action="/admin/settings/{org}"', 1)[1].split("</form>", 1)[0]
    selected = dict(re.findall(r'<select name="(\w+)">.*?<option value="(\w+)" selected', block, re.S))
    enabled = re.findall(r'name="enabled" value="(\w+)" checked', block)
    return {"mode": selected["mode"], "strategy": selected["strategy"], "language": selected["language"],
            "detection": selected.get("detection", "standard"), "enabled": enabled}


def test_strict_mode_switch_blocks_then_mask_mode_restores(gateway, admin_auth, user_key, mock_provider):
    html = gateway.get("/admin/settings", auth=admin_auth).text
    original = _org_form(html, "default")
    all_types = re.findall(r'name="enabled" value="(\w+)"', html.split('action="/admin/settings/default"', 1)[1].split("</form>", 1)[0])

    def save(mode: str, enabled: list[str]) -> None:
        r = gateway.post("/admin/settings/default", auth=admin_auth, follow_redirects=False,
                         data={**original, "mode": mode, "enabled": enabled, "csrf": _csrf(html)})
        assert r.status_code == 303, r.text

    prompt = "Kérlek, írj Kovács Péternek a szerződésről."
    save("strict", all_types)
    try:
        deadline = time.monotonic() + 30
        while True:  # the gateway caches settings for ~10 s
            mock_provider.clear()
            status, _, body = chat(gateway, user_key, prompt)
            if status == 400:
                assert "privacy_hub_sensitive_data" in json.dumps(body) and "PERSON" in json.dumps(body)
                assert "Kovács" not in json.dumps(body)
                assert mock_provider.received() == []
                break
            assert time.monotonic() < deadline, "strict mode never took effect"
            time.sleep(2)
        assert chat(gateway, user_key, "Mennyi 2 + 2?")[0] == 200  # nothing detected -> allowed
    finally:
        save(original["mode"], original["enabled"])
    deadline = time.monotonic() + 30
    while chat(gateway, user_key, prompt)[0] != 200:
        assert time.monotonic() < deadline, "mask mode never came back"
        time.sleep(2)


def test_maximum_detection_level_masks_unknown_names_end_to_end(gateway, admin_auth, user_key, mock_provider):
    html = gateway.get("/admin/settings", auth=admin_auth).text
    original = _org_form(html, "default")

    def save(detection: str) -> None:
        r = gateway.post("/admin/settings/default", auth=admin_auth, follow_redirects=False,
                         data={**original, "detection": detection, "csrf": _csrf(html)})
        assert r.status_code == 303, r.text

    # an invented name no list knows, and an order code: only "maximum" masks them
    prompt = "Holnap a Zebrafa Studióval egyeztetünk a TQ-88412/B rendelésről."
    save("maximum")
    try:
        deadline = time.monotonic() + 30
        while True:  # the gateway caches settings for ~10 s
            mock_provider.clear()
            status, text, _ = chat(gateway, user_key, prompt)
            assert status == 200
            sent = json.dumps(mock_provider.received(), ensure_ascii=False)
            if "Zebrafa" not in sent:
                break
            assert time.monotonic() < deadline, "maximum detection never took effect"
            time.sleep(2)
        assert "TQ-88412" not in sent and "[NAME_1]-vel" in sent and "[CODE_1]" in sent
        assert text == f"Mock reply: {prompt}"  # restored in full
        assert 'value="maximum" selected' in gateway.get("/admin/settings", auth=admin_auth).text
    finally:
        save(original["detection"])
    deadline = time.monotonic() + 30
    while True:
        mock_provider.clear()
        chat(gateway, user_key, prompt)
        if "Zebrafa" in json.dumps(mock_provider.received(), ensure_ascii=False):
            break
        assert time.monotonic() < deadline, "standard detection never came back"
        time.sleep(2)


def test_portal_creates_user_with_budget_and_key(gateway, admin_auth):
    html = gateway.get("/admin/budgets", auth=admin_auth).text
    user_id = f"budget-user-{uuid.uuid4().hex[:8]}"
    r = gateway.post("/admin/budgets/user", auth=admin_auth, data={
        "csrf": _csrf(html), "user_id": user_id, "org_id": "default", "max_budget": "0.0000001", "budget_duration": "",
    })
    assert r.status_code == 200, r.text
    key = re.search(r"(sk-[A-Za-z0-9_\-]+)", r.text).group(1)
    try:
        assert chat(gateway, key, "Szia!")[0] == 200
        blocked = _blocked_by_budget(gateway, key)
        assert "budget" in json.dumps(blocked["body"]).lower(), blocked
        assert user_id in gateway.get("/admin/budgets", auth=admin_auth).text
    finally:
        litellm_api("POST", "/user/delete", {"user_ids": [user_id]})


def test_audit_page_lists_events_without_text(gateway, admin_auth, user_key):
    pii = _unique_pii()
    assert chat(gateway, user_key, f"Írj a {pii['email']} címre.")[0] == 200
    page = gateway.get("/admin/audit", auth=admin_auth).text
    assert "EMAIL_ADDRESS" in page and "completed" in page
    assert pii["email"] not in page


def test_preview_api_masks_without_sending(gateway, user_key, mock_provider):
    r = gateway.post("/api/preview", headers={"Authorization": f"Bearer {user_key}"},
                     json={"messages": [{"role": "user", "content": "Írj Kovács Péternek a kp@example.com címre."}]})
    assert r.status_code == 200, r.text
    data = r.json()
    masked = data["masked_messages"][0]["content"]
    assert "Kovács" not in masked and "kp@example.com" not in masked
    assert data["entity_counts"].get("EMAIL_ADDRESS") == 1
    assert "mapping" not in data
    assert mock_provider.received() == []
    bad = gateway.post("/api/preview", headers={"Authorization": "Bearer sk-not-a-key"},
                       json={"messages": [{"role": "user", "content": "x"}]})
    assert bad.status_code == 401


def test_chat_page_is_served(gateway):
    r = gateway.get("/chat")
    assert r.status_code == 200 and "chat.js" in r.text
