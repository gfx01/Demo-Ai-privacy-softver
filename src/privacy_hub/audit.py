"""Append-only audit log.

What is recorded: timestamp, organisation, user/team/key alias, model, number
and types of masked entities, outcome, latency, error class.

What is NEVER recorded: prompt text, response text, entity values, the
placeholder mapping, exception messages (they can quote input). The
``AuditEvent`` type simply has no field that could carry them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .db import Database

log = logging.getLogger("privacy_hub.audit")


@dataclass(frozen=True)
class AuditEvent:
    organisation_id: str
    request_id: str
    event: str  # "request" | "response"
    outcome: str
    user_id: str | None = None
    team_id: str | None = None
    key_alias: str | None = None
    end_user_id: str | None = None
    model: str | None = None
    mode: str | None = None
    strategy: str | None = None
    language: str | None = None
    entity_counts: dict[str, int] = field(default_factory=dict)
    restored_total: int | None = None
    leftover_tokens: int | None = None
    latency_ms: int | None = None
    error_class: str | None = None

    @property
    def entity_total(self) -> int:
        return sum(self.entity_counts.values())


class AuditUnavailableError(RuntimeError):
    pass


class AuditSink:
    async def record(self, event: AuditEvent) -> None:
        raise NotImplementedError


class LogAuditSink(AuditSink):
    """Development fallback: writes the (PII-free) event to the log."""

    async def record(self, event: AuditEvent) -> None:
        log.info("audit %s", json.dumps(event.__dict__, sort_keys=True))


class MemoryAuditSink(AuditSink):
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)


class PostgresAuditSink(AuditSink):
    SQL = """
        INSERT INTO audit_events (
            organisation_id, request_id, event, outcome, user_id, team_id, key_alias,
            end_user_id, model, mode, strategy, language, entity_total, entity_counts,
            restored_total, leftover_tokens, latency_ms, error_class
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, event: AuditEvent) -> None:
        try:
            await self._db.execute(
                self.SQL,
                (
                    event.organisation_id,
                    event.request_id,
                    event.event,
                    event.outcome,
                    event.user_id,
                    event.team_id,
                    event.key_alias,
                    event.end_user_id,
                    event.model,
                    event.mode,
                    event.strategy,
                    event.language,
                    event.entity_total,
                    json.dumps(event.entity_counts, sort_keys=True),
                    event.restored_total,
                    event.leftover_tokens,
                    event.latency_ms,
                    event.error_class,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise AuditUnavailableError(f"audit write failed: {type(exc).__name__}") from exc
