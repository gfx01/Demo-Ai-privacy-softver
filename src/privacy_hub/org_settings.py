"""Per-organisation privacy settings (strict vs mask mode, strategy, ...)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .config import Config
from .db import Database

log = logging.getLogger("privacy_hub.settings")


@dataclass(frozen=True)
class OrgSettings:
    organisation_id: str
    mode: str  # "mask": replace and forward; "strict": block if anything is detected
    strategy: str  # "bracket" | "pseudonym"
    language: str  # "hu" | "en"
    disabled_entities: tuple[str, ...] = field(default_factory=tuple)
    # "standard" | "maximum" (also masks unknown words; see docs/LIMITATIONS.md)
    detection: str = "standard"


class SettingsUnavailableError(RuntimeError):
    pass


class SettingsProvider:
    """Reads ``org_settings`` from Postgres with a short cache. Falls back to
    environment defaults only for organisations without a row; if the
    database cannot be read at all, raises (the guardrail fails closed)."""

    def __init__(self, cfg: Config, db: Database | None) -> None:
        self._cfg = cfg
        self._db = db
        self._cache: dict[str, tuple[float, OrgSettings]] = {}

    def defaults(self, org_id: str) -> OrgSettings:
        return OrgSettings(
            organisation_id=org_id,
            mode=self._cfg.default_mode,
            strategy=self._cfg.default_strategy,
            language=self._cfg.default_language,
            detection=self._cfg.default_detection,
        )

    async def get(self, org_id: str) -> OrgSettings:
        now = time.monotonic()
        hit = self._cache.get(org_id)
        if hit and hit[0] > now:
            return hit[1]
        if self._db is None:
            settings = self.defaults(org_id)
        else:
            try:
                row = await self._db.fetchone(
                    "SELECT mode, strategy, language, disabled_entities, detection FROM org_settings WHERE organisation_id = %s",
                    (org_id,),
                )
            except Exception as exc:  # noqa: BLE001 - any DB error must block
                raise SettingsUnavailableError(f"cannot read org settings: {type(exc).__name__}") from exc
            if row is None:
                settings = self.defaults(org_id)
            else:
                settings = OrgSettings(org_id, row[0], row[1], row[2], tuple(row[3] or ()), row[4])
        self._cache[org_id] = (now + self._cfg.settings_cache_s, settings)
        return settings
