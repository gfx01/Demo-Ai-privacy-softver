"""Hungarian recognizer registry."""

from __future__ import annotations

import re

from presidio_analyzer import EntityRecognizer
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    EmailRecognizer,
    IbanRecognizer,
    IpRecognizer,
    PhoneRecognizer,
)

from .hu_ids import (
    BankAccountRecognizer,
    BirthDateRecognizer,
    CompanyRegRecognizer,
    IdCardRecognizer,
    LicensePlateRecognizer,
    PersonalIdRecognizer,
    TajRecognizer,
    TaxIdRecognizer,
    VatNumberRecognizer,
)
from .hu_names import HuNameRecognizer
from .hu_places import HuPlaceRecognizer
from .hu_text import (
    HuAddressRecognizer,
    HuCompanyRecognizer,
    HuInstitutionRecognizer,
    HuPhoneRecognizer,
    HuStreetRecognizer,
    MoneyRecognizer,
)


class AnyTldEmailRecognizer(EmailRecognizer):
    """Presidio's EmailRecognizer only accepts domains whose suffix is in the
    public suffix list, so addresses on internal domains (``ceg.local``,
    ``corp.internal``) or reserved ones (``.example``) were not detected at
    all. Any alphabetic or punycode top-level label is accepted here; the
    cost is occasional over-masking (``logo@2x.png``), the safe direction."""

    TLD_RE = re.compile(r"\.(?:[^\W\d_]{2,63}|xn--[a-z0-9-]{2,59})$", re.IGNORECASE)

    def validate_result(self, pattern_text: str) -> bool:
        return bool(self.TLD_RE.search(pattern_text))


PATTERN_RECOGNIZERS = (
    TajRecognizer,
    TaxIdRecognizer,
    VatNumberRecognizer,
    BankAccountRecognizer,
    CompanyRegRecognizer,
    IdCardRecognizer,
    PersonalIdRecognizer,
    LicensePlateRecognizer,
    BirthDateRecognizer,
    HuPhoneRecognizer,
    HuAddressRecognizer,
    HuStreetRecognizer,
    HuCompanyRecognizer,
    HuInstitutionRecognizer,
    MoneyRecognizer,
)


def hungarian_recognizers(*, gazetteer: bool = True, lexicon_dir: str | None = None) -> list[EntityRecognizer]:
    recognizers: list[EntityRecognizer] = [cls(supported_language="hu") for cls in PATTERN_RECOGNIZERS]
    recognizers += [
        AnyTldEmailRecognizer(supported_language="hu"),
        IbanRecognizer(supported_language="hu"),
        CreditCardRecognizer(supported_language="hu"),
        IpRecognizer(supported_language="hu"),
        PhoneRecognizer(supported_regions=("HU", "AT", "DE", "SK", "RO", "UK", "US"), supported_language="hu"),
    ]
    if gazetteer:
        recognizers.append(HuNameRecognizer(lexicon_dir=lexicon_dir, supported_language="hu"))
        recognizers.append(HuPlaceRecognizer(lexicon_dir=lexicon_dir, supported_language="hu"))
    return recognizers


__all__ = ["hungarian_recognizers", "PATTERN_RECOGNIZERS", "HuNameRecognizer", "HuPlaceRecognizer", "AnyTldEmailRecognizer"]
