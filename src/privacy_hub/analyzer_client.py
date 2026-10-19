"""HTTP client for the analyzer service (Presidio analyzer + anonymizer)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .masking.model import Mapping


class AnalyzerError(RuntimeError):
    """Any failure to obtain a trustworthy masking result. Callers must treat
    this as "do not forward the request" (fail closed)."""


@dataclass
class MaskResponse:
    masked_texts: list[str]
    mapping: Mapping
    entity_counts: dict[str, int]
    language: str
    elapsed_ms: float

    @property
    def entity_total(self) -> int:
        return sum(self.entity_counts.values())


class AnalyzerClient:
    def __init__(self, base_url: str, token: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s),
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    async def mask(
        self,
        texts: list[str],
        *,
        language: str,
        strategy: str,
        entities: list[str] | None = None,
        org_id: str | None = None,
    ) -> MaskResponse:
        payload: dict[str, Any] = {"texts": texts, "language": language, "strategy": strategy}
        if entities is not None:
            payload["entities"] = entities
        if org_id is not None:
            payload["org_id"] = org_id
        try:
            resp = await self._client.post("/v1/mask", json=payload)
        except httpx.HTTPError as exc:  # timeouts, connection refused, ...
            raise AnalyzerError(f"analyzer unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise AnalyzerError(f"analyzer returned HTTP {resp.status_code}")
        try:
            body = resp.json()
            masked = body["masked_texts"]
            mapping = Mapping.from_dict(body["mapping"])
            counts = {str(k): int(v) for k, v in body.get("entity_counts", {}).items()}
            result = MaskResponse(
                masked_texts=masked,
                mapping=mapping,
                entity_counts=counts,
                language=str(body.get("language", language)),
                elapsed_ms=float(body.get("elapsed_ms", 0.0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalyzerError("analyzer returned a malformed response") from exc
        if not isinstance(masked, list) or len(masked) != len(texts) or not all(isinstance(t, str) for t in masked):
            raise AnalyzerError("analyzer response does not match the request")
        return result

    async def ready(self) -> bool:
        try:
            resp = await self._client.get("/health/ready")
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    async def aclose(self) -> None:
        await self._client.aclose()
