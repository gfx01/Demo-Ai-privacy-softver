"""Hungarian recognizer registry."""

from __future__ import annotations

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
from .hu_text import (
    HuAddressRecognizer,
    HuCompanyRecognizer,
    HuInstitutionRecognizer,
    HuPhoneRecognizer,
    MoneyRecognizer,
)

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
    HuCompanyRecognizer,
    HuInstitutionRecognizer,
    MoneyRecognizer,
)


def hungarian_recognizers(*, gazetteer: bool = True, lexicon_dir: str | None = None) -> list[EntityRecognizer]:
    recognizers: list[EntityRecognizer] = [cls(supported_language="hu") for cls in PATTERN_RECOGNIZERS]
    recognizers += [
        EmailRecognizer(supported_language="hu"),
        IbanRecognizer(supported_language="hu"),
        CreditCardRecognizer(supported_language="hu"),
        IpRecognizer(supported_language="hu"),
        PhoneRecognizer(supported_regions=("HU", "AT", "DE", "SK", "RO", "UK", "US"), supported_language="hu"),
    ]
    if gazetteer:
        recognizers.append(HuNameRecognizer(lexicon_dir=lexicon_dir, supported_language="hu"))
    return recognizers


__all__ = ["hungarian_recognizers", "PATTERN_RECOGNIZERS", "HuNameRecognizer"]
