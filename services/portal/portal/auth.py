"""Admin authentication (SSO-ready).

The demo uses HTTP Basic auth against PORTAL_ADMIN_USER / PORTAL_ADMIN_PASSWORD.
``AUTH_PROVIDER=oidc`` is reserved for an OpenID Connect provider (Entra ID,
Google Workspace, Keycloak); the configuration keys are read and validated,
but the login flow is NOT implemented in this MVP (see TODO.md).

Because browsers resend Basic credentials automatically, every state-changing
form carries a CSRF token bound to the user and the day.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

# Per-process secret for CSRF tokens (tokens become invalid on restart).
_CSRF_SECRET = os.environ.get("PORTAL_CSRF_SECRET", "").encode() or secrets.token_bytes(32)


@dataclass(frozen=True)
class Admin:
    username: str
    organisation_id: str | None = None  # None = all organisations (platform admin)


class OIDCSettings:
    """Placeholder for SSO. Keys: OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET,
    OIDC_REDIRECT_URI, OIDC_ADMIN_GROUP, OIDC_ORG_CLAIM."""

    KEYS = ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI", "OIDC_ADMIN_GROUP", "OIDC_ORG_CLAIM")

    def __init__(self) -> None:
        self.values = {k: os.environ.get(k, "") for k in self.KEYS}

    @property
    def configured(self) -> bool:
        return all(self.values[k] for k in self.KEYS[:4])


def require_admin(request: Request) -> Admin:
    provider = os.environ.get("AUTH_PROVIDER", "basic")
    if provider == "oidc":
        raise HTTPException(status_code=501, detail="OIDC login is not implemented in this MVP (see TODO.md)")
    user = os.environ.get("PORTAL_ADMIN_USER", "admin")
    password = os.environ.get("PORTAL_ADMIN_PASSWORD", "")
    if len(password) < 12:
        raise HTTPException(status_code=503, detail="PORTAL_ADMIN_PASSWORD is not configured")
    header = request.headers.get("authorization", "")
    challenge = {"WWW-Authenticate": 'Basic realm="privacy-hub admin", charset="UTF-8"'}
    if not header.lower().startswith("basic "):
        raise HTTPException(status_code=401, headers=challenge)
    try:
        supplied_user, _, supplied_pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=401, headers=challenge) from None
    ok_user = hmac.compare_digest(supplied_user.encode(), user.encode())
    ok_pw = hmac.compare_digest(supplied_pw.encode(), password.encode())
    if not (ok_user and ok_pw):
        time.sleep(0.3)  # slow down guessing
        raise HTTPException(status_code=401, headers=challenge)
    return Admin(username=user)


def csrf_token(admin: Admin) -> str:
    day = str(int(time.time() // 86400))
    mac = hmac.new(_CSRF_SECRET, f"{admin.username}|{day}".encode(), hashlib.sha256).hexdigest()
    return f"{day}.{mac}"


def check_csrf(admin: Admin, token: str | None) -> None:
    if not token or "." not in token:
        raise HTTPException(status_code=403, detail="missing CSRF token")
    day, mac = token.split(".", 1)
    try:
        age = int(time.time() // 86400) - int(day)
    except ValueError:
        raise HTTPException(status_code=403, detail="bad CSRF token") from None
    expected = hmac.new(_CSRF_SECRET, f"{admin.username}|{day}".encode(), hashlib.sha256).hexdigest()
    if age not in (0, 1) or not hmac.compare_digest(mac, expected):
        raise HTTPException(status_code=403, detail="bad CSRF token")
