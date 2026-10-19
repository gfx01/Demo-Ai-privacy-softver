"""Thin client for the LiteLLM management API (internal network, master key)."""

from __future__ import annotations

import os
from typing import Any

import httpx

# Models end users may call. The canary model is for the gateway self-test
# only; mock-error is for tests (reachable with the master key only).
BASE_MODELS = ["mock-gpt", "mock-inflect", "privacy-preview"]


def user_models() -> list[str]:
    """Base models plus provider wildcards enabled in PROVIDER_MODELS
    (e.g. "openai/*,anthropic/*") once the matching API key is configured."""
    extra = [m.strip() for m in os.environ.get("PROVIDER_MODELS", "").split(",") if m.strip()]
    return BASE_MODELS + [m for m in extra if m not in BASE_MODELS]


class LiteLLMAdmin:
    def __init__(self, base_url: str | None = None, master_key: str | None = None, timeout: float = 15.0) -> None:
        self.base_url = (base_url or os.environ.get("LITELLM_URL", "http://litellm:4000")).rstrip("/")
        key = master_key or os.environ.get("LITELLM_MASTER_KEY", "")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, headers={"Authorization": f"Bearer {key}"})

    def _get(self, path: str, **params: Any) -> Any:
        r = self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        r = self._client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def live(self) -> bool:
        try:
            return self._client.get("/health/liveliness").status_code == 200
        except httpx.HTTPError:
            return False

    # teams ------------------------------------------------------------------
    def team_info(self, team_id: str) -> dict | None:
        r = self._client.get("/team/info", params={"team_id": team_id})
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.json()

    def list_teams(self) -> list[dict]:
        data = self._get("/team/list")
        return data if isinstance(data, list) else data.get("teams", [])

    def create_team(self, team_id: str, alias: str, organisation_id: str, max_budget: float | None = None,
                    budget_duration: str | None = None) -> dict:
        body: dict[str, Any] = {
            "team_id": team_id,
            "team_alias": alias,
            "metadata": {"organisation_id": organisation_id},
            "models": user_models(),
        }
        if max_budget is not None:
            body["max_budget"] = max_budget
        if budget_duration:
            body["budget_duration"] = budget_duration
        return self._post("/team/new", body)

    def sync_team_models(self, team_id: str) -> dict:
        return self._post("/team/update", {"team_id": team_id, "models": user_models()})

    def update_team_budget(self, team_id: str, max_budget: float | None, budget_duration: str | None) -> dict:
        return self._post("/team/update", {"team_id": team_id, "max_budget": max_budget, "budget_duration": budget_duration})

    # keys / users -------------------------------------------------------------
    def key_info(self, key: str) -> dict | None:
        r = self._client.get("/key/info", params={"key": key})
        if r.status_code in (400, 401, 403, 404):
            return None
        r.raise_for_status()
        return r.json()

    def generate_key(self, *, team_id: str, alias: str, organisation_id: str, user_id: str | None = None,
                     key: str | None = None, max_budget: float | None = None, budget_duration: str | None = None) -> dict:
        # No per-key model list: keys inherit the team's models.
        body: dict[str, Any] = {
            "team_id": team_id,
            "key_alias": alias,
            "metadata": {"organisation_id": organisation_id},
        }
        if user_id:
            body["user_id"] = user_id
        if key:
            body["key"] = key
        if max_budget is not None:
            body["max_budget"] = max_budget
        if budget_duration:
            body["budget_duration"] = budget_duration
        return self._post("/key/generate", body)

    def list_users(self) -> list[dict]:
        data = self._get("/user/list")
        return data.get("users", data) if isinstance(data, dict) else data

    def create_user(self, user_id: str, *, max_budget: float | None, budget_duration: str | None,
                    team_id: str | None = None) -> dict:
        body: dict[str, Any] = {"user_id": user_id, "auto_create_key": False}
        if max_budget is not None:
            body["max_budget"] = max_budget
        if budget_duration:
            body["budget_duration"] = budget_duration
        if team_id:
            body["teams"] = [team_id]
        return self._post("/user/new", body)

    def update_user_budget(self, user_id: str, max_budget: float | None, budget_duration: str | None) -> dict:
        return self._post("/user/update", {"user_id": user_id, "max_budget": max_budget, "budget_duration": budget_duration})

    def user_info(self, user_id: str) -> dict | None:
        r = self._client.get("/user/info", params={"user_id": user_id})
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.json()
