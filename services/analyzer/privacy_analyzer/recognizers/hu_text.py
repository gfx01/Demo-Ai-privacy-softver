"""Phone numbers, postal addresses, company names and money amounts.

Phone formats follow libphonenumber's HU metadata (country code 36, trunk
prefix 06, Budapest "1" + 7 digits, other areas 2 digits + 6, mobile
20/30/31/50/70 + 7). Addresses, company names and money are best-effort
heuristics; their limits are documented in docs/LIMITATIONS.md.
"""

from __future__ import annotations

import re

from presidio_analyzer import RecognizerResult

from .base import NA, NB, HuRegexRecognizer, Pattern

UPPER = "A-ZÁÉÍÓÖŐÚÜŰ"
LOWER = "a-záéíóöőúüű"

_PREFIX = r"(?:\+\s?36|0036|06)"
_SEP = r"[\s\-/.]*"
_MOBILE = r"(?:20|21|30|31|50|70)"
_AREA = r"(?:2[2-9]|3[2-7]|4[24-9]|5[2-79]|6[23689]|7[2-9]|8[2-57-9]|9[2-69])"


class HuPhoneRecognizer(HuRegexRecognizer):
    ENTITY = "PHONE_NUMBER"
    PATTERNS = (
        Pattern(
            "mobile",
            NB + rf"(?P<value>(?:\(\s?)?{_PREFIX}{_SEP}\(?\s?{_MOBILE}\s?\)?{_SEP}(?:\d{{3}}[\s\-.]?\d{{4}}|\d{{4}}[\s\-.]?\d{{3}}|\d{{2}}[\s\-]\d{{2}}[\s\-]\d{{3}}))" + NA,
            0.7,
        ),
        Pattern(
            "budapest",
            NB + rf"(?P<value>(?:\(\s?)?{_PREFIX}{_SEP}\(?\s?1\s?\)?{_SEP}(?:\d{{3}}[\s\-.]?\d{{4}}|\d{{4}}[\s\-.]?\d{{3}}|\d{{3}}[\s\-]\d{{2}}[\s\-]\d{{2}}))" + NA,
            0.65,
        ),
        Pattern(
            "geographic",
            NB + rf"(?P<value>(?:\(\s?)?{_PREFIX}{_SEP}\(?\s?{_AREA}\s?\)?{_SEP}(?:\d{{3}}[\s\-.]?\d{{3}}|\d{{2}}[\s\-]\d{{2}}[\s\-]\d{{2}}))" + NA,
            0.6,
        ),
        Pattern("e164", NB + r"(?P<value>\+36\d{8,9})" + NA, 0.7),
    )
    CONTEXT = r"\btel|telefon|mobil|hív|\bfax\b|elérhető|szám(?:on|a)"
    CONTEXT_BOOST = 0.2

    def value_span(self, match: re.Match) -> tuple[int, int]:
        start, end = match.span("value")
        # "(06 1) 234 5678": keep the parenthesis balanced inside the span
        text = match.string
        if text[start] == "(" and ")" not in text[start:end]:
            start += 1
        return start, end


# Street types ("közterület jelleg") as commonly written, with abbreviations.
_STREET_TYPES = (
    r"utca|u\.|út|útja|ú\.|tér|tere|körút|krt\.|köz|sor|fasor|sétány|rakpart|rkp\.|park|dűlő|lakótelep|ltp\.|"
    r"liget|lejtő|lépcső|udvar|sugárút|határút|telep|tanya|major|pusztaház|kert|kertsor|árok|átjáró|"
    r"hegy|domb|part|zug|köre|forduló|kapu|tető|szél|ösvény|főút|országút"
)
_HOUSE = r"\d{1,4}(?:\s?[/\-–]\s?\d{1,4})?(?:\s?/?\s?[A-Za-z])?\.?"
_FLOOR = rf"(?:,?\s*(?:\d{{1,2}}\.\s?(?:em(?:elet)?\.?|lph\.?)|fszt\.?|földszint|félemelet|mfszt\.?)\s*(?:\d{{1,3}}\.?\s?(?:ajtó|a\.)?)?(?:\s*\d{{1,3}}\.)?)?"
_STREET_NAME = rf"(?:[{UPPER}0-9][{LOWER}A-Za-z0-9\-\.]*\s+){{0,4}}"
_CITY = rf"[{UPPER}][{LOWER}\-]+(?:[\s\-][{UPPER}][{LOWER}]+)?(?:\s+[IVXL]+\.(?:\s?ker(?:ület)?\.?)?)?"


class HuAddressRecognizer(HuRegexRecognizer):
    ENTITY = "HU_ADDRESS"
    PATTERNS = (
        # 1052 Budapest, Váci utca 12. (3. em. 4.)
        Pattern(
            "postcode_city_street",
            rf"(?<!\d)(?P<value>[1-9]\d{{3}}\s+{_CITY}\s*,?\s+{_STREET_NAME}(?:{_STREET_TYPES})\s+{_HOUSE}{_FLOOR})",
            0.75,
        ),
        # Budapest, Váci u. 12., 1052
        Pattern(
            "city_street_postcode",
            rf"(?P<value>{_CITY}\s*,\s+{_STREET_NAME}(?:{_STREET_TYPES})\s+{_HOUSE}{_FLOOR}(?:\s*,?\s*[1-9]\d{{3}})?)(?![\d])",
            0.6,
        ),
        # Váci utca 12. (street + number only)
        Pattern(
            "street_number",
            rf"(?P<value>(?<![{LOWER}{UPPER}])[{UPPER}][{LOWER}\-]+(?:\s+[{UPPER}0-9][{LOWER}A-Za-z\-\.]*){{0,3}}\s+(?:{_STREET_TYPES})\s+\d{{1,4}}(?:\s?[/\-–]\s?\d{{1,4}})?(?:\s?/?\s?[A-Za-z](?![{LOWER}]))?\.?{_FLOOR})",
            0.5,
        ),
        # Pf. 123, 1052 Budapest   /  postcode + city alone needs context
        Pattern("po_box", rf"(?P<value>(?:Pf\.|Postafiók)\s?\d{{1,5}}\.?(?:\s*,?\s*[1-9]\d{{3}}\s+{_CITY})?)", 0.5),
        Pattern("postcode_city", rf"(?<!\d)(?P<value>[1-9]\d{{3}}\s+{_CITY})(?=[\s,.;)]|$)", 0.3, needs_context=True),
    )
    CONTEXT = r"\bcím|lakcím|lakik|székhely|telephely|levelezési|tartózkodási|lakóhely|\bcímre|\bcímen|\bcímű|szállítás"
    CONTEXT_BOOST = 0.15
    FLAGS = re.UNICODE

    def analyze(self, text, entities, nlp_artifacts=None):
        results = super().analyze(text, entities, nlp_artifacts)
        cleaned = []
        for r in results:
            # Trailing sentence period belongs to the sentence unless it is the
            # ordinal dot of a house/door number ("12." is how Hungarians write it).
            value = text[r.start : r.end].rstrip(" ,")
            cleaned.append(RecognizerResult(r.entity_type, r.start, r.start + len(value), r.score, r.analysis_explanation))
        return cleaned


# Company legal forms (case-insensitive in chat style: "kft.", "KFT").
_LEGAL_FORMS = (
    r"Nonprofit\s+(?:Kft|Zrt|Bt)\.?|Kft\.?|Zrt\.?|Nyrt\.?|Bt\.?|Kkt\.?|Rt\.?|Kht\.?|Kv\.?|Szövetkezet|"
    r"Ügyvédi\s+Iroda|Közjegyzői\s+Iroda|Könyvelőiroda|Könyvelő\s+Iroda|e\.\s?v\.|Egyéni\s+Cég|"
    r"Ltd\.?|GmbH|LLC|Inc\.?|S\.?A\.|AG|B\.?V\."
)
# Capitalised words that start sentences but are not part of a company name.
_STOP = {
    "A", "Az", "Egy", "Ma", "Tegnap", "Holnap", "Kedves", "Tisztelt", "Kérem", "Kérlek", "Szia", "Sziasztok",
    "Üdv", "Üdvözlettel", "Ezért", "Továbbá", "Valamint", "Mivel", "Hogy", "Ha", "Mert", "És", "Sajnos",
    "Köszönöm", "Köszönjük", "Re", "Fw", "Tárgy", "Ügyfél", "Partner", "Megrendelő", "Vállalkozó", "Szállító",
    "Vevő", "Eladó", "Bérlő", "Bérbeadó", "Számla", "Cég", "Cégnév", "Név", "Kiállító", "Vevő:", "Írj",
    "Foglald", "Fogalmazz", "Készíts", "Segíts", "Nézd", "Ellenőrizd", "Kft", "Zrt", "Bt",
}


class HuCompanyRecognizer(HuRegexRecognizer):
    """Company names ending in a legal form. Best effort."""

    ENTITY = "HU_COMPANY"
    # No "." inside words: a dot would let a match run across a sentence end.
    _WORD = rf"(?:[{UPPER}0-9][\w&'’\-]*)"
    _JOIN = r"(?:\s+(?:és|and|&|\+|-)\s+|\s+|\s?-\s?)"
    PATTERNS = (
        Pattern(
            "legal_form",
            rf"(?<![\w])(?P<value>{_WORD}(?:{_JOIN}{_WORD}){{0,6}}\s+(?i:{_LEGAL_FORMS}))(?![\w])",
            0.6,
        ),
    )
    FLAGS = re.UNICODE

    def analyze(self, text, entities, nlp_artifacts=None):
        out = []
        for r in super().analyze(text, entities, nlp_artifacts):
            start, end = r.start, r.end
            # Drop leading sentence words ("Tegnap Zöldmező Kft." -> "Zöldmező Kft.")
            while True:
                m = re.match(r"([^\s]+)\s+", text[start:end])
                if not m or m.group(1).rstrip(",:") not in _STOP:
                    break
                start += m.end()
            if not re.search(r"\s", text[start:end].strip()):
                continue  # only a legal form left
            if not text[end - 1 : end] == "." and text[end : end + 1] == ".":
                end += 1  # "Kft" + sentence period: keep abbreviation dot
            out.append(RecognizerResult(r.entity_type, start, end, r.score, r.analysis_explanation))
        return out


_INSTITUTION_HEADS = (
    r"Iroda|Kórház|Rendelőintézet|Rendelő|Magánrendelő|Klinika|Bíróság|Járásbíróság|Törvényszék|Ítélőtábla|"
    r"Egyetem|Főiskola|Iskola|Gimnázium|Óvoda|Hivatal|Kormányhivatal|Önkormányzat|Alapítvány|Egyesület|"
    r"Szövetség|Kamara|Bank|Takarékszövetkezet|Biztosító|Patika|Gyógyszertár|Hotel|Szálloda|Étterem|Intézet|"
    r"Központ|Ügynökség|Stúdió|Szalon|Kereskedés|Áruház|Klub|Társaság|Csoport|Holding"
)


class HuInstitutionRecognizer(HuRegexRecognizer):
    """Named organisations without a legal form: "Mecsek Utazási Irodától",
    "Mátravidéki Kórházban". Best effort (needs capitalised words)."""

    ENTITY = "ORGANIZATION"
    PATTERNS = (
        Pattern(
            "head_noun",
            rf"(?<![\w])(?P<value>(?:[{UPPER}][\w\-]*\s+){{1,5}}(?:{_INSTITUTION_HEADS})[{LOWER}]{{0,8}})(?![\w])",
            0.55,
        ),
    )

    def analyze(self, text, entities, nlp_artifacts=None):
        out = []
        for r in super().analyze(text, entities, nlp_artifacts):
            start = r.start
            while True:  # drop leading sentence words ("A Mátravidéki Kórházban")
                m = re.match(r"([^\s]+)\s+", text[start : r.end])
                if not m or m.group(1).rstrip(",:") not in _STOP:
                    break
                start += m.end()
            if re.search(r"\s", text[start : r.end].strip()):
                out.append(RecognizerResult(r.entity_type, start, r.end, r.score, r.analysis_explanation))
        return out


class MoneyRecognizer(HuRegexRecognizer):
    """Amounts with a currency keyword. Best effort; masking amounts stops the
    model from computing with them (documented trade-off)."""

    ENTITY = "MONEY"
    # 1 250 000 | 1.250.000,50 | 1,450.00 (English) | 1250  (thousands separator: space, NBSP or dot)
    _NUM = r"\d{1,3}(?:[ \u00a0.]\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?"
    # written-out amounts: "négymillió-nyolcszázötvenezer forint"
    _WORDS = r"[a-záéíóöőúüű\-]*(?:ezer|millió|milliárd|száz)[a-záéíóöőúüű\-]*"
    # "Ft" without a trailing dot: "1 250 000 Ft." is usually a sentence end.
    _CUR = r"Ft|HUF|forint(?:ot|ra|ról|tal|nak|ért)?|EUR|euró(?:t|ra|ról|val|nak|ért)?|€|USD|\$|CHF|GBP|£"
    PATTERNS = (
        Pattern(
            "amount_currency",
            NB + rf"(?P<value>(?:{_NUM})(?:,-)?\s?(?:(?:ezer|millió|milliárd|e\.|M)\s?)?(?:{_CUR}))(?![\w])",
            0.55,
        ),
        Pattern("currency_amount", rf"(?<![\w])(?P<value>(?:€|\$|£|EUR|HUF|USD|CHF)\s?(?:{_NUM}))" + NA, 0.55),
        Pattern("words_currency", rf"(?<![\w])(?P<value>{_WORDS}\s+(?:forint\w*|Ft|euró\w*|EUR|HUF))(?![\w])", 0.5),
    )
