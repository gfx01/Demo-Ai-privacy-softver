"""Portal access to the privacyhub database (role ph_portal: read audit,
manage organisations and their privacy settings)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

ENTITY_CHOICES = [
    ("PERSON", "Személynév / person name"),
    ("HU_COMPANY", "Cégnév / company name"),
    ("ORGANIZATION", "Szervezet (NER) / organisation"),
    ("LOCATION", "Helynév / place name"),
    ("HU_ADDRESS", "Postai cím / postal address"),
    ("EMAIL_ADDRESS", "E-mail"),
    ("PHONE_NUMBER", "Telefonszám / phone"),
    ("HU_TAJ", "TAJ szám"),
    ("HU_TAX_ID", "Adóazonosító jel"),
    ("HU_VAT_NUMBER", "Adószám"),
    ("HU_BANK_ACCOUNT", "Bankszámlaszám"),
    ("IBAN_CODE", "IBAN"),
    ("HU_COMPANY_REG_NUMBER", "Cégjegyzékszám"),
    ("HU_ID_CARD", "Személyi igazolvány szám"),
    ("HU_PERSONAL_ID", "Személyi azonosító"),
    ("HU_LICENSE_PLATE", "Rendszám / licence plate"),
    ("HU_BIRTH_DATE", "Születési dátum / birth date"),
    ("MONEY", "Összeg / amount (breaks arithmetic)"),
    ("CREDIT_CARD", "Bankkártya / credit card"),
    ("IP_ADDRESS", "IP cím"),
]
OUTCOMES = ["forwarded", "completed", "blocked_strict", "blocked_error", "blocked_unsupported_content",
            "blocked_unsupported_route", "provider_error", "preview", "completed_no_mapping"]


@contextmanager
def connect():
    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"], row_factory=dict_row, connect_timeout=5) as conn:
        yield conn


def organisations() -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(
            "SELECT o.id, o.name, s.mode, s.strategy, s.language, s.disabled_entities, s.detection, s.updated_at, s.updated_by "
            "FROM organisations o LEFT JOIN org_settings s ON s.organisation_id = o.id ORDER BY o.id"
        ).fetchall()


def create_organisation(org_id: str, name: str, admin: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO organisations (id, name) VALUES (%s, %s)", (org_id, name))
        conn.execute(
            "INSERT INTO org_settings (organisation_id, updated_by) VALUES (%s, %s)", (org_id, admin)
        )


def update_settings(org_id: str, mode: str, strategy: str, language: str, disabled: list[str], detection: str,
                    admin: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE org_settings SET mode=%s, strategy=%s, language=%s, disabled_entities=%s, detection=%s, "
            "updated_at=now(), updated_by=%s WHERE organisation_id=%s",
            (mode, strategy, language, disabled, detection, admin, org_id),
        )


def settings_for(org_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute(
            "SELECT mode, strategy, language, disabled_entities, detection FROM org_settings WHERE organisation_id=%s",
            (org_id,),
        ).fetchone()


def audit_page(org: str | None, outcome: str | None, user: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
    where, params = [], []
    if org:
        where.append("organisation_id = %s")
        params.append(org)
    if outcome:
        where.append("outcome = %s")
        params.append(outcome)
    if user:
        where.append("(user_id = %s OR end_user_id = %s OR key_alias = %s)")
        params += [user, user, user]
    sql = (
        "SELECT ts, organisation_id, request_id, event, outcome, user_id, team_id, key_alias, end_user_id, model, "
        "mode, strategy, language, entity_total, entity_counts, restored_total, latency_ms, error_class "
        "FROM audit_events " + ("WHERE " + " AND ".join(where) if where else "") +
        " ORDER BY ts DESC, id DESC LIMIT %s OFFSET %s"
    )
    with connect() as conn:
        return conn.execute(sql, (*params, limit, offset)).fetchall()


def audit_summary(org: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(
            "SELECT organisation_id, outcome, count(*) AS n, sum(entity_total) AS entities "
            "FROM audit_events WHERE ts > now() - interval '30 days' "
            + ("AND organisation_id = %s " if org else "")
            + "GROUP BY organisation_id, outcome ORDER BY organisation_id, outcome",
            (org,) if org else (),
        ).fetchall()
