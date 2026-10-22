"""Analyzer service: Presidio analyzer + anonymizer behind a small REST API.

Internal service: it must only be reachable from the gateway and the portal
(docker network) and requires a bearer token. It never logs request text.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .engine import DETECTION_LEVELS, PrivacyEngine

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("privacy_analyzer")

MAX_TEXTS = 256
MAX_TOTAL_CHARS = int(os.environ.get("ANALYZER_MAX_TOTAL_CHARS", "200000"))

state: dict[str, PrivacyEngine] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.environ.get("ANALYZER_API_TOKEN", "")
    if len(token) < 16:
        raise RuntimeError("ANALYZER_API_TOKEN must be set (>= 16 chars)")
    if len(os.environ.get("PRIVACY_HUB_PSEUDONYM_KEY", "")) < 16:
        raise RuntimeError("PRIVACY_HUB_PSEUDONYM_KEY must be set (>= 16 chars)")
    state["engine"] = PrivacyEngine()
    yield
    state.clear()


app = FastAPI(title="privacy-hub analyzer", lifespan=lifespan, docs_url=None, redoc_url=None)


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("ANALYZER_API_TOKEN", "")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid token")


class MaskRequest(BaseModel):
    texts: list[str] = Field(max_length=MAX_TEXTS)
    language: str = "hu"
    strategy: str = "bracket"
    entities: list[str] | None = None
    disabled_entities: list[str] = Field(default_factory=list, max_length=64)
    detection: str = "standard"
    org_id: str | None = None
    include_mapping: bool = True


class AnalyzeRequest(BaseModel):
    text: str
    language: str = "hu"
    entities: list[str] | None = None
    score_threshold: float = 0.4


@app.get("/health/live")
def live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict:
    engine = state.get("engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="not ready")
    return {"status": "ready", "languages": engine.languages, "nlp_backends": engine.nlp_backends}


@app.post("/v1/analyze", dependencies=[Depends(require_token)])
def analyze(req: AnalyzeRequest) -> list[dict]:
    engine = state["engine"]
    if req.language not in engine.languages:
        raise HTTPException(status_code=400, detail="unsupported language")
    results = engine.analyze(req.text, req.language, req.entities, req.score_threshold)
    return [{"entity_type": r.entity_type, "start": r.start, "end": r.end, "score": round(r.score, 3)} for r in results]


@app.post("/v1/mask", dependencies=[Depends(require_token)])
def mask(req: MaskRequest) -> dict:
    engine = state["engine"]
    if sum(len(t) for t in req.texts) > MAX_TOTAL_CHARS:
        raise HTTPException(status_code=413, detail="request too large")
    if req.language not in engine.languages:
        raise HTTPException(status_code=400, detail="unsupported language")
    if req.strategy not in {"bracket", "pseudonym", "bracket_naive"}:
        raise HTTPException(status_code=400, detail="unsupported strategy")
    if req.detection not in DETECTION_LEVELS or (req.detection == "maximum" and engine.maximum is None):
        raise HTTPException(status_code=400, detail="unsupported detection level")
    started = time.perf_counter()
    result = engine.mask(req.texts, req.language, req.strategy, req.entities, disabled_entities=req.disabled_entities,
                         detection=req.detection)
    elapsed = (time.perf_counter() - started) * 1000
    # Log sizes and counts only - never text.
    log.info("mask texts=%d chars=%d entities=%d detection=%s ms=%.1f", len(req.texts), sum(map(len, req.texts)),
             result.entity_total, req.detection, elapsed)
    body = {
        "masked_texts": result.masked_texts,
        "entity_counts": result.entity_counts,
        "language": req.language,
        "elapsed_ms": round(elapsed, 1),
    }
    body["mapping"] = result.mapping.to_dict() if req.include_mapping else {"version": 1, "strategy": req.strategy, "entries": []}
    return body
