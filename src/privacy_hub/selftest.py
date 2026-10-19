"""Runtime proof that the guardrail masks and restores.

Run by the gateway entrypoint once LiteLLM is listening. Sends the canary
prompt to the in-process ``privacy-canary`` model (non-streaming and
streaming). Passes only if

* the canary provider saw no canary secret in clear text (masking active),
* the reply delivered to the client contains the canary email again
  (restoration active, including streaming).

On failure the entrypoint stops LiteLLM: the gateway refuses to run.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

from .providers import CANARY_EMAIL, CANARY_PROMPT

MARKER = os.environ.get("PRIVACY_HUB_SELFTEST_MARKER", "/tmp/privacy_hub_selftest_ok")


def _check(reply: str, label: str) -> list[str]:
    errors = []
    if "CANARY leaked=0" not in reply:
        errors.append(f"{label}: provider received clear-text canary data or unexpected reply")
    if CANARY_EMAIL not in reply:
        errors.append(f"{label}: placeholder was not restored in the reply")
    return errors


def run(base_url: str, api_key: str, model: str, wait_s: float) -> list[str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.monotonic() + wait_s
    with httpx.Client(base_url=base_url, timeout=30) as client:
        while True:
            try:
                if client.get("/health/liveliness").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                return ["gateway did not become live in time"]
            time.sleep(1)

        body = {"model": model, "messages": [{"role": "user", "content": CANARY_PROMPT}]}
        errors: list[str] = []
        r = client.post("/v1/chat/completions", json=body, headers=headers)
        if r.status_code != 200:
            return [f"non-streaming canary request failed with HTTP {r.status_code}"]
        errors += _check(r.json()["choices"][0]["message"]["content"] or "", "non-streaming")

        text = []
        with client.stream("POST", "/v1/chat/completions", json={**body, "stream": True}, headers=headers) as s:
            if s.status_code != 200:
                return errors + [f"streaming canary request failed with HTTP {s.status_code}"]
            for line in s.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                for choice in json.loads(line[6:]).get("choices", []):
                    text.append((choice.get("delta") or {}).get("content") or "")
        errors += _check("".join(text), "streaming")
        return errors


def main() -> int:
    base = os.environ.get("PRIVACY_HUB_SELFTEST_URL", "http://127.0.0.1:4000")
    key = os.environ.get("LITELLM_MASTER_KEY", "")
    model = os.environ.get("PRIVACY_HUB_CANARY_MODEL", "privacy-canary")
    wait_s = float(os.environ.get("PRIVACY_HUB_SELFTEST_WAIT_S", "180"))
    try:
        os.remove(MARKER)
    except FileNotFoundError:
        pass
    errors = run(base, key, model, wait_s)
    if errors:
        print("PRIVACY SELF-TEST FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    with open(MARKER, "w", encoding="utf-8") as fh:
        fh.write(str(int(time.time())))
    print("privacy self-test passed (masking + restore, streaming + non-streaming)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
