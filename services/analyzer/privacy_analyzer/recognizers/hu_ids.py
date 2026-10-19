"""Hungarian identifiers. Check digits are enforced only where the algorithm
is confirmed (see privacy_hub.hu_ids.PROVENANCE); the rest is format only."""

from __future__ import annotations

from privacy_hub import hu_ids

from .base import NA, NB, HuRegexRecognizer, Pattern


class TajRecognizer(HuRegexRecognizer):
    """TAJ szám. Check digit: law (1996. évi XX. tv. 2. melléklet)."""

    ENTITY = "HU_TAJ"
    PATTERNS = (Pattern("taj", NB + r"(?P<value>\d{3}[ -]?\d{3}[ -]?\d{3})" + NA, 0.3),)
    CONTEXT = r"\btaj\b|taj[- ]?sz|társadalombiztosítási|tb[- ]?szám|biztosítási azonosító"
    ACCEPT_INVALID_WITH_CONTEXT = True
    checksum = staticmethod(hu_ids.is_valid_taj)


class TaxIdRecognizer(HuRegexRecognizer):
    """Adóazonosító jel. Check digit: library-documented (see PROVENANCE)."""

    ENTITY = "HU_TAX_ID"
    PATTERNS = (Pattern("tax_id", NB + r"(?P<value>8(?:\d{9}|[ -]\d{5}[ -]\d{3}[ -]\d|\d{4}[ -]\d{4}[ -]?\d))" + NA, 0.35),)
    CONTEXT = r"adóazonosító|adó ?azonosító|\badóaz|\bad\. ?az|tax ?id"
    ACCEPT_INVALID_WITH_CONTEXT = True
    checksum = staticmethod(hu_ids.is_valid_tax_id)


class VatNumberRecognizer(HuRegexRecognizer):
    """Adószám (8-1-2) and EU VAT (HU + 8). Törzsszám check digit:
    library-documented (python-stdnum hu.anum, NAV client code)."""

    ENTITY = "HU_VAT_NUMBER"
    PATTERNS = (
        Pattern("adoszam_812", NB + r"(?P<value>\d{8}[-‐–]\d[-‐–]\d{2})" + NA, 0.5),
        Pattern("eu_vat", NB + r"(?P<value>HU[ -]?\d{8})" + NA, 0.5),
        Pattern("adoszam_11", NB + r"(?P<value>\d{11})" + NA, 0.3, needs_context=True),
    )
    CONTEXT = r"adószám|adószámú|adószáma|közösségi adó|\bvat\b|áfa|\btax\b"
    checksum = staticmethod(hu_ids.is_valid_adoszam)

    def validate(self, value: str) -> bool | None:
        v = value.upper().replace(" ", "").replace("-", "")
        if v.startswith("HU"):
            return hu_ids.is_valid_vat_core(v[2:])
        return hu_ids.is_valid_adoszam(value)


class BankAccountRecognizer(HuRegexRecognizer):
    """Pénzforgalmi jelzőszám (2×8 / 3×8). Check digits: library-documented."""

    ENTITY = "HU_BANK_ACCOUNT"
    PATTERNS = (
        Pattern("giro_grouped", NB + r"(?P<value>\d{8}[-‐– ]\d{8}(?:[-‐– ]\d{8})?)" + NA, 0.5),
        Pattern("giro_plain", NB + r"(?P<value>\d{16}(?:\d{8})?)" + NA, 0.3, needs_context=True),
    )
    CONTEXT = r"számlaszám|bankszámla|számlá(?:ra|ról|n|t|ja|nk)|bszla|\bszla\b|account|átutal|giro|pénzforgalmi"
    checksum = staticmethod(hu_ids.is_valid_bank_account)


class CompanyRegRecognizer(HuRegexRecognizer):
    """Cégjegyzékszám: format only (no check digit known)."""

    ENTITY = "HU_COMPANY_REG_NUMBER"
    PATTERNS = (
        Pattern(
            "cg",
            NB + r"(?P<value>(?:Cg\.\s?)?(?:0[1-9]|1\d|20)[-‐–](?:0[1-9]|1\d|2[0-3])[-‐–]\d{6})" + NA,
            0.55,
        ),
    )
    CONTEXT = r"cégjegyzék|\bcg\.|nyilvántartási szám|cégbíróság|törvényszék mint cégbíróság"


class IdCardRecognizer(HuRegexRecognizer):
    """Személyi igazolvány szám: format only (secondary sources)."""

    ENTITY = "HU_ID_CARD"
    PATTERNS = (
        Pattern("card", NB + r"(?P<value>\d{6}[ -]?[A-Z]{2})(?![\w])", 0.45),
        Pattern("booklet", NB + r"(?P<value>[A-Z]{2}[ -]?(?:[IVX]+[ -])?\d{6})(?![\w])", 0.3, needs_context=True),
    )
    CONTEXT = r"személyi ig|személyi\b|\bszig|igazolvány|okmányazonosító|szem\. ?ig|id ?card|útlevél"
    FLAGS = 0  # case-sensitive: the letters are upper case


class PersonalIdRecognizer(HuRegexRecognizer):
    """Személyi azonosító (M-YYMMDD-SSSK): format only, context required."""

    ENTITY = "HU_PERSONAL_ID"
    PATTERNS = (
        Pattern(
            "szemelyi_szam",
            NB + r"(?P<value>[1-8][ -]?\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[ -]?\d{4})" + NA,
            0.4,
            needs_context=True,
        ),
    )
    CONTEXT = r"személyi szám|személyi azonosító|szem\. ?sz|személyazonosító"


class LicensePlateRecognizer(HuRegexRecognizer):
    """Forgalmi rendszám: format only."""

    ENTITY = "HU_LICENSE_PLATE"
    PATTERNS = (
        Pattern("plate_2022", NB + r"(?P<value>[A-Z]{2}[ -]?[A-Z]{2}[-‐–]\d{3})(?![\w])", 0.5),
        Pattern("plate_classic", NB + r"(?P<value>[A-Z]{3}[-‐–]\d{3})(?![\w])", 0.45),
        Pattern("plate_nohyphen", NB + r"(?P<value>[A-Z]{3} ?\d{3}|[A-Z]{4} ?\d{3})(?![\w])", 0.3, needs_context=True),
    )
    CONTEXT = r"rendszám|\bfrsz|forgalmi|gépjármű|gépkocsi|\bautó|kocsi|jármű|parkol"
    FLAGS = 0


class BirthDateRecognizer(HuRegexRecognizer):
    """Birth dates (only with context such as "született", "szül.")."""

    ENTITY = "HU_BIRTH_DATE"
    MONTHS = (
        r"január|február|március|április|május|június|július|augusztus|szeptember|október|november|december|"
        r"jan\.|febr?\.|márc\.|ápr\.|máj\.|jún\.|júl\.|aug\.|szept\.|okt\.|nov\.|dec\."
    )
    PATTERNS = (
        Pattern(
            "date_text",
            NB + rf"(?P<value>(?:19|20)\d{{2}}\.\s?(?:{MONTHS})\s?\d{{1,2}}\.?)",
            0.4,
            needs_context=True,
        ),
        Pattern(
            "date_num",
            NB + r"(?P<value>(?:19|20)\d{2}[.\-/]\s?(?:0?[1-9]|1[0-2])[.\-/]\s?(?:0?[1-9]|[12]\d|3[01])\.?)",
            0.4,
            needs_context=True,
        ),
    )
    CONTEXT = r"születe|\bszül\.|\bszd\.|szül\. ?idő|birth|\bsz\. ?d|életkor|\bkora\b"
    CONTEXT_BEFORE = 40
    CONTEXT_AFTER = 5
