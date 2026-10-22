"""Placeholder labels per entity type (shown to the LLM in bracket tokens)."""

from __future__ import annotations

import re

# Presidio / custom entity type -> short label used inside bracket tokens.
# Labels must be unique per entity type and match LABEL_RE.
LABELS: dict[str, str] = {
    "PERSON": "PERSON",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "LOCATION": "LOCATION",
    "ORGANIZATION": "ORG",
    "IBAN_CODE": "IBAN",
    "CREDIT_CARD": "CARD",
    "IP_ADDRESS": "IP",
    "URL": "URL",
    "DATE_TIME": "DATE",
    "NRP": "NRP",
    "US_SSN": "SSN",
    "HU_COMPANY": "COMPANY",
    "HU_TAJ": "TAJ",
    "HU_TAX_ID": "TAX_ID",
    "HU_VAT_NUMBER": "ADOSZAM",
    "HU_BANK_ACCOUNT": "ACCOUNT",
    "HU_COMPANY_REG_NUMBER": "CEGJEGYZEK",
    "HU_ID_CARD": "ID_CARD",
    "HU_PERSONAL_ID": "SZEMELYI_SZAM",
    "HU_ADDRESS": "ADDRESS",
    "HU_LICENSE_PLATE": "PLATE",
    "HU_PHONE_NUMBER": "PHONE",
    "MONEY": "AMOUNT",
}

LABEL_RE = re.compile(r"^[A-Z][A-Z0-9_]*[A-Z0-9]$")


def label_for(entity_type: str) -> str:
    label = LABELS.get(entity_type)
    if label is None:
        label = re.sub(r"[^A-Z0-9_]", "_", entity_type.upper()).strip("_") or "ENTITY"
        if label.startswith("HU_"):
            label = label[3:]
    return label


def entity_types_for_label(label: str) -> list[str]:
    return [t for t, lab in LABELS.items() if lab == label]
