"""Fixtures for tests against the running docker compose stack.

Run: docker compose up -d --build && pytest -m integration tests/integration
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


ENV = _load_env()
GATEWAY = os.environ.get("PRIVACY_HUB_GATEWAY", f"http://127.0.0.1:{ENV.get('GATEWAY_PORT', '8080')}")


def compose(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, check=check, timeout=timeout)


def _stack_up() -> bool:
    try:
        return httpx.get(f"{GATEWAY}/healthz", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def pytest_collection_modifyitems(config, items):
    if _stack_up():
        return
    skip = pytest.mark.skip(reason=f"compose stack not reachable at {GATEWAY} (run: docker compose up -d --build)")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    return ENV


@pytest.fixture(scope="session")
def gateway() -> httpx.Client:
    with httpx.Client(base_url=GATEWAY, timeout=60) as client:
        yield client


@pytest.fixture(scope="session")
def user_key(env) -> str:
    return env["OPENWEBUI_LITELLM_KEY"]


@pytest.fixture(scope="session")
def master_key(env) -> str:
    return env["LITELLM_MASTER_KEY"]


class MockProvider:
    """Reads what the mock LLM provider received (via docker compose exec, so
    the inspection endpoint never needs to be published on the host)."""

    SCRIPT = (
        "import urllib.request,json,os,sys;"
        "r=urllib.request.Request('http://127.0.0.1:8080/_mock/requests',method=sys.argv[1],"
        "headers={'Authorization':'Bearer '+os.environ['MOCK_LLM_INSPECT_TOKEN']});"
        "print(urllib.request.urlopen(r).read().decode())"
    )

    def clear(self) -> None:
        compose("exec", "-T", "mock-llm", "python", "-c", self.SCRIPT, "DELETE")

    def received(self) -> list[dict]:
        out = compose("exec", "-T", "mock-llm", "python", "-c", self.SCRIPT, "GET").stdout
        return [r["body"] for r in json.loads(out)]


@pytest.fixture
def mock_provider() -> MockProvider:
    m = MockProvider()
    m.clear()
    return m


def chat(client: httpx.Client, key: str, content: str, model: str = "mock-gpt", stream: bool = False, **extra) -> tuple[int, str, dict]:
    body = {"model": model, "messages": [{"role": "user", "content": content}], "stream": stream, **extra}
    headers = {"Authorization": f"Bearer {key}"}
    if not stream:
        r = client.post("/v1/chat/completions", json=body, headers=headers)
        data = r.json()
        text = data["choices"][0]["message"]["content"] if r.status_code == 200 else ""
        return r.status_code, text, data
    parts: list[str] = []
    with client.stream("POST", "/v1/chat/completions", json=body, headers=headers) as r:
        if r.status_code != 200:
            r.read()
            return r.status_code, "", r.json()
        for line in r.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                for c in json.loads(line[6:]).get("choices", []):
                    parts.append((c.get("delta") or {}).get("content") or "")
    return 200, "".join(parts), {}


def wait_healthy(service: str, timeout_s: float = 180) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        out = compose("ps", "--format", "{{.Health}}", service, check=False).stdout.strip()
        if out == "healthy":
            return
        time.sleep(2)
    raise AssertionError(f"{service} did not become healthy within {timeout_s}s")


def psql(db: str, sql: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", db, "-At", "-c", sql).stdout


def litellm_api(method: str, path: str, body: dict | None = None) -> dict:
    """Call the LiteLLM admin API from inside its container (admin routes are
    not reachable through the gateway)."""
    script = (
        "import json,os,sys,urllib.request;"
        "b=sys.argv[3].encode() if sys.argv[3] else None;"
        "r=urllib.request.Request('http://127.0.0.1:4000'+sys.argv[2],data=b,method=sys.argv[1],"
        "headers={'Authorization':'Bearer '+os.environ['LITELLM_MASTER_KEY'],'Content-Type':'application/json'});"
        "print(urllib.request.urlopen(r).read().decode())"
    )
    out = compose("exec", "-T", "litellm", "python3", "-c", script, method, path, json.dumps(body) if body else "")
    return json.loads(out.stdout)


@pytest.fixture(scope="session")
def admin_auth(env) -> tuple[str, str]:
    return env.get("PORTAL_ADMIN_USER", "admin"), env["PORTAL_ADMIN_PASSWORD"]
