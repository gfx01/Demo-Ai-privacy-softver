"""Hungarian recognizer registry."""

from __future__ import annotations

import re

import phonenumbers
from presidio_analyzer import EntityRecognizer
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    EmailRecognizer,
    IbanRecognizer,
    IpRecognizer,
    PhoneRecognizer,
)

from .hu_ids import (
    BankAccountOcrRecognizer,
    BankAccountRecognizer,
    BirthDateRecognizer,
    CompanyRegRecognizer,
    IbanOcrRecognizer,
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
    OcrEmailRecognizer,
)


class AnyTldEmailRecognizer(EmailRecognizer):
    """Presidio's EmailRecognizer only accepts domains whose suffix is in the
    public suffix list, so addresses on internal domains (``ceg.local``,
    ``corp.internal``) or reserved ones (``.example``) were not detected at
    all. Any alphabetic or punycode top-level label is accepted here; the
    cost is occasional over-masking (``logo@2x.png``), the safe direction."""

    TLD_RE = re.compile(r"\.(?:[^\W\d_]{2,63}|xn--[a-z0-9-]{2,59})$", re.IGNORECASE)
    # A Hungarian case ending after the address: "kovacs@ceg.hu-ra",
    # "...@biztoskez.example-re". The e-mail pattern reads the ending as part of
    # the domain, the TLD check then failed and the whole address leaked.
    SUFFIX_RE = re.compile(r"-[a-záéíóöőúüű]{1,6}$")

    def validate_result(self, pattern_text: str) -> bool:
        return bool(self.TLD_RE.search(pattern_text) or self.TLD_RE.search(self.SUFFIX_RE.sub("", pattern_text)))

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        for r in results:
            value = text[r.start : r.end]
            m = self.SUFFIX_RE.search(value)
            if m and not self.TLD_RE.search(value):
                r.end -= len(m.group(0))  # the ending stays outside: "[EMAIL_1]-ra"
        return results


class GuardedPhoneRecognizer(PhoneRecognizer):
    """libphonenumber matching for foreign numbers (Presidio), without the
    matches that are references or dates, not phone numbers: part of a longer
    code ("3.P.21.077/2025", "SZ-2026/001184"), starting with a date or year
    ("2026.10.12. 21:40") or ending in a year ("077/2025"), and bare digit runs
    without a trunk or international prefix ("ref: 7745012938"). Hungarian
    numbers are found by HuPhoneRecognizer independently of this filter."""

    PREFIX = re.compile(r"^(?:\+|00|0|\()")
    YEAR_START = re.compile(r"^(?:19|20)\d{2}\s?[./\-]")
    YEAR_END = re.compile(r"/\s?(?:19|20)\d{2}$")
    # libphonenumber is run on short windows that can hold a number with a
    # prefix, not on the whole text: on long digit runs ("1/1/1/...") the full
    # scan took seconds per 20,000 characters.
    WINDOW = re.compile(r"(?<![\w+])(?:\+|00|0|\()[\d\s()./\-–]{5,24}\d")

    def _matches(self, text):
        found = []
        for w in self.WINDOW.finditer(text):
            window = text[w.start() : w.end()]
            for region in self.supported_regions:
                for m in phonenumbers.PhoneNumberMatcher(window, region, leniency=self.leniency):
                    r = self._get_recognizer_result(m, window, region, None)
                    r.start, r.end = w.start() + m.start, w.start() + m.end
                    found.append(r)
        return EntityRecognizer.remove_duplicates(found)

    def analyze(self, text, entities, nlp_artifacts=None):
        out = []
        for r in self._matches(text):
            value = text[r.start : r.end]
            before = text[r.start - 1] if r.start > 0 else " "
            after = text[r.end] if r.end < len(text) else " "
            if before.isalnum() or before in "-./" or after.isalnum() or after in "/-":
                continue
            if self.YEAR_START.match(value) or self.YEAR_END.search(value) or not self.PREFIX.match(value):
                continue
            out.append(r)
        return out


PATTERN_RECOGNIZERS = (
    TajRecognizer,
    TaxIdRecognizer,
    VatNumberRecognizer,
    BankAccountRecognizer,
    BankAccountOcrRecognizer,
    IbanOcrRecognizer,
    CompanyRegRecognizer,
    IdCardRecognizer,
    PersonalIdRecognizer,
    LicensePlateRecognizer,
    BirthDateRecognizer,
    HuPhoneRecognizer,
    OcrEmailRecognizer,
    HuAddressRecognizer,
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
        GuardedPhoneRecognizer(supported_regions=("HU", "AT", "DE", "SK", "RO", "UK", "US"), supported_language="hu"),
    ]
    recognizers.append(HuStreetRecognizer(supported_language="hu", lexicon_dir=lexicon_dir))
    if gazetteer:
        recognizers.append(HuNameRecognizer(lexicon_dir=lexicon_dir, supported_language="hu"))
        recognizers.append(HuPlaceRecognizer(lexicon_dir=lexicon_dir, supported_language="hu"))
    return recognizers


__all__ = ["hungarian_recognizers", "PATTERN_RECOGNIZERS", "HuNameRecognizer", "HuPlaceRecognizer", "AnyTldEmailRecognizer",
           "GuardedPhoneRecognizer"]
