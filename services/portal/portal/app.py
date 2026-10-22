"""Portal: landing page, admin pages and the preview-before-send chat."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import store
from .auth import Admin, check_csrf, csrf_token, require_admin
from .litellm_admin import LiteLLMAdmin

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="privacy-hub portal", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

ORG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
USER_ID_RE = re.compile(r"^[A-Za-z0-9@._+-]{2,128}$")
DURATIONS = {"": None, "1d": "1d", "7d": "7d", "30d": "30d", "1mo": "1mo"}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


def litellm() -> LiteLLMAdmin:
    return LiteLLMAdmin()


def render(request: Request, name: str, admin: Admin | None = None, **ctx: Any) -> HTMLResponse:
    if admin is not None:
        ctx["csrf"] = csrf_token(admin)
        ctx["admin"] = admin
    return templates.TemplateResponse(request, name, ctx)


def _money(value: Any) -> str:
    return "–" if value is None else f"${float(value):.4f}"


templates.env.filters["money"] = _money


# --- public pages -------------------------------------------------------------
@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render(request, "index.html", openwebui_url=os.environ.get("PUBLIC_OPENWEBUI_URL", "http://localhost:3000"))


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return render(request, "chat.html")


# --- preview API (end users, authenticated with their LiteLLM virtual key) -----
class PreviewRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(max_length=200)


@app.post("/api/preview")
def preview(req: PreviewRequest, authorization: Annotated[str | None, Header()] = None) -> JSONResponse:
    key = (authorization or "").removeprefix("Bearer ").strip()
    if not key.startswith("sk-"):
        raise HTTPException(status_code=401, detail="missing API key")
    info = litellm().key_info(key)
    if info is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    meta = (info.get("info") or {}).get("metadata") or {}
    org_id = meta.get("organisation_id") or os.environ.get("PRIVACY_HUB_DEFAULT_ORG", "default")
    settings = store.settings_for(org_id) or {"mode": "mask", "strategy": "bracket", "language": "hu", "disabled_entities": []}
    texts, where = [], []
    for i, m in enumerate(req.messages):
        content = m.get("content")
        if isinstance(content, str):
            texts.append(content)
            where.append(i)
    try:
        r = httpx.post(
            os.environ.get("PRIVACY_HUB_ANALYZER_URL", "http://analyzer:8000") + "/v1/mask",
            json={"texts": texts, "language": settings["language"], "strategy": settings["strategy"],
                  "disabled_entities": list(settings["disabled_entities"] or []), "org_id": org_id,
                  "include_mapping": False},
            headers={"Authorization": f"Bearer {os.environ.get('PRIVACY_HUB_ANALYZER_TOKEN', '')}"},
            timeout=15,
        )
        r.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="PII detection unavailable") from None
    body = r.json()
    masked = [dict(m) for m in req.messages]
    for idx, text in zip(where, body["masked_texts"], strict=True):
        masked[idx]["content"] = text
    total = sum(body["entity_counts"].values())
    return JSONResponse({
        "masked_messages": masked,
        "entity_counts": body["entity_counts"],
        "mode": settings["mode"],
        "strategy": settings["strategy"],
        "would_block": settings["mode"] == "strict" and total > 0,
    })


# --- admin pages -----------------------------------------------------------------
@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, admin: Admin = Depends(require_admin)):
    error = None
    try:
        teams = litellm().list_teams()
    except httpx.HTTPError as exc:
        teams, error = [], f"LiteLLM unavailable: {type(exc).__name__}"
    return render(request, "admin_home.html", admin, orgs=store.organisations(), summary=store.audit_summary(),
                  teams=teams, error=error)


@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings(request: Request, admin: Admin = Depends(require_admin), saved: str | None = None):
    return render(request, "admin_settings.html", admin, orgs=store.organisations(), entities=store.ENTITY_CHOICES, saved=saved)


@app.post("/admin/settings/{org_id}")
async def admin_settings_save(org_id: str, request: Request, admin: Admin = Depends(require_admin)):
    form = await request.form()
    check_csrf(admin, form.get("csrf"))
    mode, strategy, language = form.get("mode"), form.get("strategy"), form.get("language")
    if mode not in ("mask", "strict") or strategy not in ("bracket", "pseudonym") or language not in ("hu", "en"):
        raise HTTPException(status_code=400, detail="invalid settings")
    allowed = {e for e, _ in store.ENTITY_CHOICES}
    # Checked boxes = entities that are masked; everything else is disabled.
    enabled = {v for v in form.getlist("enabled") if v in allowed}
    store.update_settings(org_id, mode, strategy, language, sorted(allowed - enabled), admin.username)
    return RedirectResponse(f"/admin/settings?saved={org_id}", status_code=303)


@app.post("/admin/organisations")
def admin_org_create(
    admin: Admin = Depends(require_admin),
    csrf: Annotated[str, Form()] = "",
    org_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
):
    check_csrf(admin, csrf)
    if not ORG_ID_RE.match(org_id) or not name.strip():
        raise HTTPException(status_code=400, detail="invalid organisation id or name")
    store.create_organisation(org_id, name.strip()[:200], admin.username)
    api = litellm()
    if api.team_info(f"org-{org_id}") is None:
        api.create_team(f"org-{org_id}", name.strip()[:200], org_id)
    return RedirectResponse("/admin/settings", status_code=303)


@app.get("/admin/budgets", response_class=HTMLResponse)
def admin_budgets(request: Request, admin: Admin = Depends(require_admin), new_key: str | None = None):
    api = litellm()
    error = None
    try:
        teams, users = api.list_teams(), api.list_users()
    except httpx.HTTPError as exc:
        teams, users, error = [], [], f"LiteLLM unavailable: {type(exc).__name__}"
    return render(request, "admin_budgets.html", admin, teams=teams, users=users, orgs=store.organisations(),
                  error=error, durations=[d for d in DURATIONS if d])


def _budget(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        amount = float(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid budget") from None
    if amount < 0 or amount > 100000:
        raise HTTPException(status_code=400, detail="budget out of range")
    return amount


@app.post("/admin/budgets/team")
def admin_team_budget(
    admin: Admin = Depends(require_admin),
    csrf: Annotated[str, Form()] = "",
    team_id: Annotated[str, Form()] = "",
    max_budget: Annotated[str, Form()] = "",
    budget_duration: Annotated[str, Form()] = "",
):
    check_csrf(admin, csrf)
    litellm().update_team_budget(team_id, _budget(max_budget), DURATIONS.get(budget_duration))
    return RedirectResponse("/admin/budgets", status_code=303)


@app.post("/admin/budgets/user", response_class=HTMLResponse)
def admin_user_create(
    request: Request,
    admin: Admin = Depends(require_admin),
    csrf: Annotated[str, Form()] = "",
    user_id: Annotated[str, Form()] = "",
    org_id: Annotated[str, Form()] = "",
    max_budget: Annotated[str, Form()] = "",
    budget_duration: Annotated[str, Form()] = "",
):
    check_csrf(admin, csrf)
    if not USER_ID_RE.match(user_id) or not ORG_ID_RE.match(org_id):
        raise HTTPException(status_code=400, detail="invalid user or organisation id")
    api = litellm()
    team_id = f"org-{org_id}"
    if api.user_info(user_id) is None:
        api.create_user(user_id, max_budget=_budget(max_budget), budget_duration=DURATIONS.get(budget_duration), team_id=team_id)
    else:
        api.update_user_budget(user_id, _budget(max_budget), DURATIONS.get(budget_duration))
    key = api.generate_key(team_id=team_id, alias=f"{user_id}-key", organisation_id=org_id, user_id=user_id)
    # The key is shown exactly once and never stored by the portal.
    return render(request, "admin_new_key.html", admin, user_id=user_id, key=key.get("key"))


@app.post("/admin/budgets/user/{user_id}")
def admin_user_budget(
    user_id: str,
    admin: Admin = Depends(require_admin),
    csrf: Annotated[str, Form()] = "",
    max_budget: Annotated[str, Form()] = "",
    budget_duration: Annotated[str, Form()] = "",
):
    check_csrf(admin, csrf)
    litellm().update_user_budget(user_id, _budget(max_budget), DURATIONS.get(budget_duration))
    return RedirectResponse("/admin/budgets", status_code=303)


@app.get("/admin/audit", response_class=HTMLResponse)
def admin_audit(
    request: Request,
    admin: Admin = Depends(require_admin),
    org: str | None = None,
    outcome: str | None = None,
    user: str | None = None,
    page: int = 0,
):
    rows = store.audit_page(org or None, outcome or None, user or None, limit=100, offset=max(0, page) * 100)
    return render(request, "admin_audit.html", admin, rows=rows, orgs=store.organisations(), outcomes=store.OUTCOMES,
                  org=org or "", outcome=outcome or "", user=user or "", page=max(0, page))
