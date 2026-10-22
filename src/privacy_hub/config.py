"""Runtime configuration from environment variables (no secrets in code)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .crypto import MappingKeyError, load_key


class FatalConfigError(RuntimeError):
    """Misconfiguration that must stop the gateway.

    Deliberately NOT a ValueError/TypeError: LiteLLM 1.105.1
    (``proxy/guardrails/init_guardrails.py::init_guardrails_v2``) catches those
    two exception types during guardrail initialisation, logs "proxy is starting
    WITHOUT this guardrail" and keeps serving traffic unprotected.
    """


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if not value else int(value)


def _float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return default if not value else float(value)


VALID_MODES = {"mask", "strict"}
VALID_STRATEGIES = {"bracket", "pseudonym", "bracket_naive"}
VALID_LANGUAGES = {"hu", "en"}


@dataclass(frozen=True)
class Config:
    analyzer_url: str
    analyzer_token: str = field(repr=False)
    analyzer_timeout_s: float
    mapping_key: bytes = field(repr=False)
    mapping_ttl_s: int
    keep_mapping_after_response: bool
    store_url: str = field(repr=False)  # may embed the Redis password
    database_url: str | None = field(repr=False)
    default_org: str
    default_language: str
    default_mode: str
    default_strategy: str
    audit_required: bool
    allow_non_text_content: bool
    preview_model: str
    canary_model: str
    settings_cache_s: float
    extra_allowed_params: frozenset[str] = frozenset()
    # Internal OCR service for images/files attached to chat messages
    # (privacy_hub.attachments); empty = attachments are blocked.
    ocr_url: str = ""
    ocr_token: str = field(default="", repr=False)
    ocr_timeout_s: float = 120.0
    ocr_max_bytes: int = 20 * 1024 * 1024
    ocr_max_attachments: int = 20
    # what the analyzer checks per request (ANALYZER_MAX_TOTAL_CHARS)
    ocr_max_text_chars: int = 200_000

    @classmethod
    def from_env(cls) -> "Config":
        try:
            key = load_key(os.environ.get("PRIVACY_HUB_MAPPING_KEY"))
        except MappingKeyError as exc:
            raise FatalConfigError(str(exc)) from exc
        cfg = cls(
            analyzer_url=os.environ.get("PRIVACY_HUB_ANALYZER_URL", "http://analyzer:8000").rstrip("/"),
            analyzer_token=os.environ.get("PRIVACY_HUB_ANALYZER_TOKEN", ""),
            analyzer_timeout_s=_float("PRIVACY_HUB_ANALYZER_TIMEOUT_S", 10.0),
            mapping_key=key,
            mapping_ttl_s=_int("PRIVACY_HUB_MAPPING_TTL_S", 300),
            keep_mapping_after_response=_bool("PRIVACY_HUB_KEEP_MAPPING_AFTER_RESPONSE", False),
            store_url=os.environ.get("PRIVACY_HUB_STORE_URL", "redis://redis:6379/0"),
            database_url=os.environ.get("PRIVACY_HUB_DATABASE_URL") or None,
            default_org=os.environ.get("PRIVACY_HUB_DEFAULT_ORG", "default"),
            default_language=os.environ.get("PRIVACY_HUB_DEFAULT_LANGUAGE", "hu"),
            default_mode=os.environ.get("PRIVACY_HUB_DEFAULT_MODE", "mask"),
            default_strategy=os.environ.get("PRIVACY_HUB_DEFAULT_STRATEGY", "bracket"),
            audit_required=_bool("PRIVACY_HUB_AUDIT_REQUIRED", True),
            allow_non_text_content=_bool("PRIVACY_HUB_ALLOW_NON_TEXT_CONTENT", False),
            preview_model=os.environ.get("PRIVACY_HUB_PREVIEW_MODEL", "privacy-preview"),
            canary_model=os.environ.get("PRIVACY_HUB_CANARY_MODEL", "privacy-canary"),
            settings_cache_s=_float("PRIVACY_HUB_SETTINGS_CACHE_S", 10.0),
            # Escape hatch for request parameters this version does not know.
            # Only add parameters that cannot carry free text.
            extra_allowed_params=frozenset(
                p.strip() for p in os.environ.get("PRIVACY_HUB_EXTRA_ALLOWED_PARAMS", "").split(",") if p.strip()
            ),
            ocr_url=os.environ.get("PRIVACY_HUB_OCR_URL", "").rstrip("/"),
            ocr_token=os.environ.get("PRIVACY_HUB_OCR_TOKEN", ""),
            ocr_timeout_s=_float("PRIVACY_HUB_OCR_TIMEOUT_S", 120.0),
            ocr_max_bytes=_int("PRIVACY_HUB_OCR_MAX_BYTES", 20 * 1024 * 1024),
            ocr_max_attachments=_int("PRIVACY_HUB_OCR_MAX_ATTACHMENTS", 20),
            ocr_max_text_chars=_int("PRIVACY_HUB_OCR_MAX_TEXT_CHARS", 200_000),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        problems = []
        if not self.analyzer_token or len(self.analyzer_token) < 16:
            problems.append("PRIVACY_HUB_ANALYZER_TOKEN must be set (>= 16 chars)")
        if self.default_mode not in VALID_MODES:
            problems.append(f"PRIVACY_HUB_DEFAULT_MODE must be one of {sorted(VALID_MODES)}")
        if self.default_strategy not in VALID_STRATEGIES:
            problems.append(f"PRIVACY_HUB_DEFAULT_STRATEGY must be one of {sorted(VALID_STRATEGIES)}")
        if self.default_language not in VALID_LANGUAGES:
            problems.append(f"PRIVACY_HUB_DEFAULT_LANGUAGE must be one of {sorted(VALID_LANGUAGES)}")
        if not 1 <= self.mapping_ttl_s <= 3600:
            problems.append("PRIVACY_HUB_MAPPING_TTL_S must be between 1 and 3600")
        if not 0.1 <= self.analyzer_timeout_s <= 60:
            problems.append("PRIVACY_HUB_ANALYZER_TIMEOUT_S must be between 0.1 and 60")
        if self.ocr_url and len(self.ocr_token) < 16:
            problems.append("PRIVACY_HUB_OCR_URL needs PRIVACY_HUB_OCR_TOKEN (>= 16 chars)")
        if not 1 <= self.ocr_timeout_s <= 600:
            problems.append("PRIVACY_HUB_OCR_TIMEOUT_S must be between 1 and 600")
        if self.audit_required and not self.database_url:
            problems.append("PRIVACY_HUB_AUDIT_REQUIRED=true needs PRIVACY_HUB_DATABASE_URL")
        if problems:
            raise FatalConfigError("; ".join(problems))
