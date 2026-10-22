"""Phone numbers, postal addresses, company names and money amounts.

Phone formats follow libphonenumber's HU metadata (country code 36, trunk
prefix 06, Budapest "1" + 7 digits, other areas 2 digits + 6, mobile
20/30/31/50/70 + 7). Addresses, company names and money are best-effort
heuristics; their limits are documented in docs/LIMITATIONS.md.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from presidio_analyzer import RecognizerResult

from .base import NA, NB, HuRegexRecognizer, Pattern

UPPER = "A-ZÁÉÍÓÖŐÚÜŰ"
LOWER = "a-záéíóöőúüű"

_PREFIX = r"(?:\+\s?36|0036|06)"
# "36 30 ...", and "+36" as OCR reads it: "--36", "136", "436"
_LOOSE_PREFIX = r"(?:--?\s?|–\s?|4-?|1)?36"
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
        Pattern(
            "loose_prefix",
            NB + rf"(?P<value>{_LOOSE_PREFIX}[\s\-/]\(?\s?(?:{_MOBILE}|1)\s?\)?[\s\-/]*(?:\d{{3}}[\s\-.]?\d{{4}}|\d{{4}}[\s\-.]?\d{{3}}))" + NA,
            0.5,
        ),
        Pattern(
            "loose_prefix_geographic",
            NB + rf"(?P<value>{_LOOSE_PREFIX}[\s\-/]\(?\s?{_AREA}\s?\)?[\s\-/]*\d{{3}}[\s\-.]?\d{{3}})" + NA,
            0.45,
        ),
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
    r"hegy|domb|part|zug|köre|forduló|kapu|tető|szél|ösvény|főút|országút|"
    r"ut|utja|ter|tere|korut|setany|lakotelep"  # typed without accents
)
_HOUSE = r"\d{1,4}(?:\s?[/\-–]\s?\d{1,4})?(?:\s?/?\s?[A-Za-z])?\.?"
# Floor / door after the house number: "3. em. 12. ajtó", "I. emelet 4.", "fsz. 1.",
# "fszt. 1/A", "2/7" (floor/door). Left out, they would stay in clear text.
_FLOOR = (
    rf"(?:,?\s*(?:(?:\d{{1,2}}|[IVX]{{1,4}})\.\s?(?:em(?:elet)?\.?|lph\.?)|fszt?\.?|földszint|félemelet|mfszt\.?)"
    rf"(?:\s*\d{{1,3}}(?:/[A-Za-z0-9]{{1,3}})?\.?\s?(?:ajtó|a\.)?)?(?:\s*\d{{1,3}}\.)?"
    rf"|,?\s+\d{{1,2}}\s?/\s?\d{{1,3}}[A-Za-z]?\.?(?![\d/])"
    rf"|,?\s+[IVX]{{1,4}}\.?\s?/\s?\d{{1,3}}[A-Za-z]?\.?(?![\d/]))?"
)
_LOWER_IN_NAME = rf"(?:(?!(?:{_STREET_TYPES}|és|a|az|meg|vagy|is)\s)[{LOWER}][{LOWER}\-]{{1,40}}\s+){{0,2}}"
_STREET_NAME = rf"(?:[{UPPER}0-9][{LOWER}A-Za-z0-9\-\.]{{0,40}}\s+{_LOWER_IN_NAME}){{0,4}}"
_CITY = rf"[{UPPER}][{LOWER}\-]{{1,40}}(?:[\s\-][{UPPER}][{LOWER}]{{1,40}})?(?:\s+[IVXL]+\.(?:\s?ker(?:ület)?\.?)?)?"


# Foreign street names: "Landstraße", "Wiener Straße", "Baker Street", "Keizersgracht"
_FL = "A-Za-zÀ-ÖØ-öø-ÿĀ-ſß"
_FOREIGN_STREET = (
    rf"(?:[A-ZÀ-ÖØ-ÞĀ-Ž][{_FL}\-]{{0,40}}(?:straße|strasse|gasse|weg|platz|allee|ring|damm|ufer|str\.|straat|laan|plein|gracht|kade)"
    rf"|(?:[A-ZÀ-ÖØ-ÞĀ-Ž][{_FL}\-]{{0,40}}\s+){{1,3}}(?:Straße|Strasse|Gasse|Weg|Platz|Allee|Ring|Str\.|Street|St\.|Road|Rd\.|"
    rf"Avenue|Ave\.|Lane|Square|ulica|ul\.))"
)
_FOREIGN_CITY = rf"[A-ZÀ-ÖØ-ÞĀ-Ž][{_FL}\-]{{1,40}}(?:\s(?:am|an|im|bei|ob|an\sder)\s[A-Z][{_FL}]{{1,40}})?"
_FOREIGN_POSTCODE = r"(?:[A-Z]{1,2}-)?(?:\d{4,5}|\d{3}\s\d{2})"


class HuAddressRecognizer(HuRegexRecognizer):
    ENTITY = "HU_ADDRESS"
    PATTERNS = (
        # 4020 Linz, Landstraße 41 / Landstraße 41, 4020 Linz (foreign addresses)
        Pattern(
            "foreign_postcode_city_street",
            rf"(?<![\w])(?P<value>{_FOREIGN_POSTCODE}\s+{_FOREIGN_CITY}\s*,\s+{_FOREIGN_STREET}\s+\d{{1,4}}[a-z]?(?:\s?[/\-]\s?\d{{1,4}})?)(?![\w])",
            0.6,
        ),
        Pattern(
            "foreign_street_postcode_city",
            rf"(?<![\w])(?P<value>{_FOREIGN_STREET}\s+\d{{1,4}}[a-z]?(?:\s?[/\-]\s?\d{{1,4}})?\s*,\s*{_FOREIGN_POSTCODE}\s+{_FOREIGN_CITY})(?![\w])",
            0.6,
        ),
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
            rf"(?P<value>(?<![{LOWER}{UPPER}])[{UPPER}][{LOWER}\-]{{1,40}}(?:\s+[{UPPER}0-9][{LOWER}A-Za-z\-\.]{{0,40}}){{0,3}}\s+(?:{_STREET_TYPES})\s+\d{{1,4}}(?:\s?[/\-–]\s?\d{{1,4}})?(?:\s?/?\s?[A-Za-z](?![{LOWER}]))?\.?{_FLOOR})",
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


class HuStreetRecognizer(HuRegexRecognizer):
    """Street names without a house number ("a Ráday utcában", "Kerepesi
    úton", "a Fő téren"): a quasi-identifier when combined with a person.
    Inside a full address the address match (containing span) wins."""

    ENTITY = "LOCATION"
    # street words that are also ordinary words: "Önhöz soron kívül", "Hosszú út áll előttünk"
    _AMBIGUOUS_TYPES = re.compile(r"^(?:sor|köz|út|tér|ut(?!c)|ter)", re.I)
    _STREET = (
        r"utcai|úti|téri|utc[aá](?:ban|ba|ból|ra|ról|n|t|nál|hoz|tól|ig)?|útj?[aá]?(?:on|ra|ról|ig|n|t|nál)?|úton|útra|útról|"
        r"t[eé]r(?:en|re|ről|t|ig)?|körút(?:on|ra|ról|ig)?|köz(?:ben|be|ből|re|ön|t)?|sor(?:on|ra|ról|ba|ban)?|"
        r"fasor(?:ban|ba|ból|on|ra)?|sétány(?:on|ra|ról)?|rakpart(?:on|ra|ról)?|sugárút(?:on|ra|ról)?|lépcső(?:n|re|ről)?|"
        r"u\.|krt\.|"
        r"uton|utra|utrol|utjan|teren|terre|terrol|koruton|korutra|utcaban|utcaba|utcabol|utcan"  # without accents
    )
    # lower case, also without accents: "a kálvin térre", "a rakoczi uton"
    _STREET_LOWER = (
        r"utc[aá](?:ban|ba|ból|bol|ra|ról|rol|n|t|nál|nal|hoz|tól|tol|ig)?|útj?[aá]?(?:on|ra|ról|ig|n|t|nál)?|úton|útra|útról|"
        r"ut(?:on|ra|rol|ja|jan|nal)|t[eé]r(?:en|re|ről|rol|t|ig|nél|nel)?|k[öo]r[uú]t(?:on|ra|ról|rol)?"
    )
    # streets named after a person with a title: "Mátyás király út", "Szent István körút"
    _TITLE_IN_STREET = r"(?:(?:király|királyné|királynő|herceg|hercegnő|fejedelem|gróf|grófnő|báró|püspök|érsek|vezér|költő)\s+)?"
    PATTERNS = (
        # one-word German street names: "a Schönaugasse és a Münzgrabenstraße kereszteződésében"
        Pattern(
            "foreign_street_word",
            r"(?<![\w])(?P<value>[A-ZÄÖÜ][a-zäöüß]{2,30}(?:gasse|straße|strasse|platz|allee|damm|ufer)"
            r"(?:ban|ben|ba|be|ból|ből|on|en|ön|n|ra|re|nál|nél|hoz|hez|tól|től|ig|t|ot|et)?)(?![\w])",
            0.45,
        ),
        Pattern(
            "lower_street",
            rf"(?<![\w])(?P<value>[{LOWER}][{LOWER}\-]{{3,40}}\s+(?:{_STREET_LOWER}))(?![\w])(?!\s+\d)",
            0.4,
        ),
        Pattern(
            "street_no_number",
            rf"(?<![\w])(?P<value>(?:[{UPPER}][{LOWER}]+(?:-[{UPPER}]?[{LOWER}]+)?\s+){{1,3}}{_TITLE_IN_STREET}(?:{_STREET}))(?![\w])(?!\s+\d)",
            0.5,
        ),
    )

    def __init__(self, supported_language: str = "hu", lexicon_dir: str | None = None) -> None:
        super().__init__(supported_language=supported_language)
        path = Path(lexicon_dir or os.environ.get("ANALYZER_LEXICON_DIR", "/app/data/lexicon")) / "common_words.txt"
        self.common = set(path.read_text(encoding="utf-8").split()) if path.exists() else set()

    @staticmethod
    def _sentence_start(text: str, start: int) -> bool:
        before = text[max(0, start - 200) : start].rstrip()
        if not before and start > 200:
            before = text[:start].rstrip()
        m = re.search(r"(\S+)\s*$", before)
        if m and m.group(1).rstrip(",:") in _STOP:  # "A Hosszú út ..." starts with an article
            before = before[: m.start()].rstrip()
        return not before or before[-1] in ".!?:\n\"„(–-"

    def _common(self, word: str) -> bool:
        low = word.lower()
        return low in self.common or any(low[:k] in self.common for k in range(len(low) - 1, max(4, len(low) - 6), -1))

    def _all_common(self, words: list[str]) -> bool:
        # exact lemmas only: "Kerepesi", "Váci", "Üllői" (place adjectives) are street names
        return bool(self.common) and all(w.lower() in self.common for w in words)

    def analyze(self, text, entities, nlp_artifacts=None):
        out = []
        for r in super().analyze(text, entities, nlp_artifacts):
            words = text[r.start : r.end].split()
            name_words, street = words[:-1], words[-1]
            if r.analysis_explanation and r.analysis_explanation.pattern_name == "foreign_street_word":
                out.append(r)
                continue
            if text[r.start].islower():
                # lower case: every name word must be a name, not an ordinary word ("a kálvin térre", not "a hosszú úton")
                if not self.common or any(self._common(w) for w in name_words):
                    continue
                out.append(r)
                continue
            if name_words and name_words[0].lower().startswith("ön"):
                continue  # "Önhöz", "Önöknek": polite pronoun, not a street name
            if self._AMBIGUOUS_TYPES.match(street) and self._all_common(name_words) and self._sentence_start(text, r.start):
                continue  # "Hosszú út áll előttünk." (mid-sentence, capitalised: "a Szabadság tér melletti")
            start = r.start
            while True:  # "A Ráday utcában" -> "Ráday utcában"
                m = re.match(r"([^\s]+)\s+", text[start : r.end])
                if not m or m.group(1).rstrip(",:") not in _STOP:
                    break
                start += m.end()
            if re.search(r"\s", text[start : r.end].strip()):
                out.append(RecognizerResult(r.entity_type, start, r.end, r.score, r.analysis_explanation))
        return out


# Company legal forms (case-insensitive in chat style: "kft.", "KFT").
_LEGAL_FORMS = (
    r"Nonprofit\s+(?:Kft|Zrt|Bt)\.?|Kft\.?|Zrt\.?|Nyrt\.?|Bt\.?|Kkt\.?|Rt\.?|Kht\.?|Kv\.?|Szövetkezet|"
    r"Ügyvédi\s+Iroda|Közjegyzői\s+Iroda|Könyvelőiroda|Könyvelő\s+Iroda|e\.\s?v\.|Egyéni\s+Cég|"
    r"és\s+Társai?|Ltd\.?|GmbH|LLC|Inc\.?|S\.?A\.|AG|B\.?V\.|s\.\s?r\.\s?o\.|Sp\.\s?z\s?o\.\s?o\.|S\.?R\.?L\.?|d\.o\.o\.|Kft-|"
    r"K\sft\.?|Z\srt\.?"  # OCR splits the legal form: "Csongrád-Mag K ft."
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
    # A hyphen without spaces belongs to the word ("Duna-Tisza"); a joining
    # hyphen has a space next to it ("Alfa - Beta"). With both readings open,
    # a chain like "1-2-3-...-60" had combinatorially many parses and froze
    # the analyzer (catastrophic backtracking); lengths are capped for the same reason.
    _WORD = rf"(?:[{UPPER}0-9][\w&'’\-]{{0,40}})"
    _JOIN = r"(?:\s+(?:és|and|&|\+|-)\s+|\s+|\s-\s?|-\s)"
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


# Head nouns of organisation names, also at the end of a compound word
# ("Magánkórházban", "Vesecentrumba", "Cégbíróságánál", "Tüdőgondozóban").
_INSTITUTION_HEAD_WORDS = (
    r"iroda|kórház|rendelőintézet|rendelő|klinika|bíróság|törvényszék|ítélőtábla|"
    r"egyetem|főiskola|iskola|gimnázium|óvoda|bölcsőde|kollégium|hivatal|önkormányzat|alapítvány|egyesület|"
    r"szövetség|kamara|bank|szövetkezet|biztosító|patika|gyógyszertár|hotel|szálloda|szálló|panzió|vendégház|"
    r"étterem|vendéglő|kávézó|cukrászda|pékség|intézet|központ|centrum|szolgálat|gondozó|egészségház|labor|"
    r"laboratórium|fogászat|ügynökség|stúdió|szalon|műhely|kereskedés|áruház|klub|társaság|csoport|holding|"
    r"könyvtár|múzeum|színház|plébánia|egyházközség|szerviz|pénztár|technikum|ház|otthon|"
    r"nyomda|autóbérlő|kölcsönző|fuvarozó|szállítmányozó|pizzéria|bisztró|bolt"
)
# English organisation names without a legal form: "Brightwater Health", "Nordic Logistics Group"
_ENGLISH_ORG_HEADS = (
    r"Health|Healthcare|Group|Holding|Holdings|Bank|Insurance|Capital|Partners|Consulting|Solutions|Services|Systems|"
    r"Logistics|Technologies|Technology|Software|Labs|Media|Energy|Pharma|Pharmaceuticals|Foods|Motors|Airlines|"
    r"Hotels|Resorts|University|College|Institute|Foundation|Association|Clinic|Hospital|Center|Centre|Agency|"
    r"Ventures|Industries|Electronics|Automotive|Trading|Investments"
)
# final a/e lengthens before endings: "Irodától", "Klinikára"
_INSTITUTION_HEADS = "|".join(
    h[:-1] + {"a": "[aá]", "e": "[eé]"}[h[-1]] if h[-1] in "ae" else h for h in _INSTITUTION_HEAD_WORDS.split("|")
)
# Possessive and case endings after the head ("Irodája", "Cégbíróságánál", "Klubbal")
_ORG_ENDING = (
    r"(?:(?:j?[aeáé]|j?[ae]i)?(?:nak|nek|nál|nél|hoz|hez|höz|tól|től|ból|ből|ban|ben|ba|be|ra|re|ról|ről|"
    r"val|vel|vá|vé|ig|ért|ként|t|n|é)|[bcdfghjklmnprstvz]?(?:al|el)|ot|et|öt|at|on|en|ön|j?[aeáé]|j?[ae]i)?"
)


class HuInstitutionRecognizer(HuRegexRecognizer):
    """Named organisations without a legal form: "Mecsek Utazási Irodától",
    "Mátravidéki Kórházban". Best effort (needs capitalised words)."""

    ENTITY = "ORGANIZATION"
    PATTERNS = (
        Pattern(
            "head_noun",
            rf"(?<![\w])(?P<value>(?:[{UPPER}][\w\-]{{0,40}}\s+(?:és\s+|&\s+)?){{1,5}}(?=[{UPPER}])[{UPPER}{LOWER}]{{0,40}}?(?i:{_INSTITUTION_HEADS}){_ORG_ENDING})(?![\w])",
            0.55,
        ),
        Pattern(
            "english_head_noun",
            rf"(?<![\w])(?P<value>(?:[A-Z][A-Za-z&'’\-]{{1,40}}\s+){{1,3}}(?:{_ENGLISH_ORG_HEADS})(?:-?(?:nak|nek|nál|nél|hoz|hez|"
            rf"tól|től|val|vel|t|ot|et|ra|re|ról|ről|ban|ben|ba|be|ból|ből|é|ért|ig|on|en|ön|n))?)(?![\w])",
            0.5,
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


_TLD = r"(?:example|hu|com|net|org|eu|info|biz|io|co|de|at|sk|ro|rs|hr|si|cz|pl|uk|nl|be|fr|it|es|ch|us|ua)"


class OcrEmailRecognizer(HuRegexRecognizer):
    """E-mail addresses as OCR (or careless typing) renders them; the regular
    recognizer needs an intact "@":
    * spaces around the at sign: "kovacs.anna @ ceg.hu"
    * "@" read as a capital O, a zero or a symbol inside a lower-case token:
      "konyvelesOrabaszamviteli. example"
    * "@" read as "o", "go" or " o" between a first.last local part and a
      domain, only next to an e-mail word: "bakos.lillagohajnal-zrt.example".
    Every part has a length limit (local part 64, label 63, at most 4 labels),
    so a long run of letters and digits cannot make the regex backtrack
    quadratically (the analyzer reads untrusted text)."""

    ENTITY = "EMAIL_ADDRESS"
    _DOMAIN = rf"[a-z0-9][a-z0-9-]{{0,62}}(?:\.[a-z0-9-]{{1,63}}){{0,3}}\.\s?{_TLD}"
    # no space before the TLD: "ment oda. de holnap" is not an address
    _DOMAIN_TIGHT = rf"[a-z0-9][a-z0-9-]{{0,62}}(?:\.[a-z0-9-]{{1,63}}){{0,3}}\.{_TLD}"
    PATTERNS = (
        Pattern(
            "spaced_at",
            rf"(?<![\w.])(?P<value>[A-Za-z0-9][A-Za-z0-9._%+-]{{0,63}}(?:\s@\s?|@\s)[A-Za-z0-9][A-Za-z0-9-]{{0,62}}"
            rf"(?:\.[A-Za-z0-9-]{{1,63}}){{0,3}}\.\s?{_TLD})(?![\w])",
            0.6,
        ),
        Pattern(
            "misread_at",
            rf"(?<![\w.])(?P<value>(?!www\.)[a-z0-9][a-z0-9._%+-]{{0,62}}[a-z0-9][O©®]{_DOMAIN})(?![\w])",
            0.5,
        ),
        Pattern(
            "name_dot_name",
            rf"(?<![\w.])(?P<value>(?!www\.)[a-z]{{2,30}}\.[a-z]{{2,30}}[0-9]{{0,4}}(?:\s?[o@q]|go)\s?{_DOMAIN_TIGHT})(?![\w])",
            0.4,
            needs_context=True,
        ),
        # "penzugy obakonyfa-kft.example": a word, then " o" glued to a domain
        Pattern(
            "word_space_o",
            rf"(?<![\w.])(?P<value>[a-z][a-z0-9._-]{{2,63}}\so{_DOMAIN_TIGHT})(?![\w])",
            0.4,
            needs_context=True,
        ),
        # an intact "@" whose domain lost its dot: "d.olah@hangfalbtexample"
        Pattern("at_without_tld", r"(?<![\w.@])(?P<value>[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9][A-Za-z0-9]{2,62})(?![\w.@])",
                0.5, needs_context=True),
    )
    CONTEXT = r"e-?mail|címre|címen|elérhetőség|levél|\bmail\b"
    CONTEXT_BEFORE = 40
    CONTEXT_AFTER = 20
    FLAGS = re.UNICODE  # case-sensitive: the capital O is the signal


class MoneyRecognizer(HuRegexRecognizer):
    """Amounts with a currency keyword. Best effort; masking amounts stops the
    model from computing with them (documented trade-off)."""

    ENTITY = "MONEY"
    # 1 250 000 | 1.250.000,50 | 1,450.00 (English) | 1250  (thousands separator: space, NBSP or dot)
    _NUM = r"\d{1,3}(?:[ \u00a0.]\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?"
    # written-out amounts: "négymillió-nyolcszázötvenezer forint"
    # bounded: an unbounded run of letters and hyphens is rescanned from every hyphen (quadratic)
    _WORDS = r"[a-záéíóöőúüű\-]{0,60}(?:ezer|millió|milliárd|száz)[a-záéíóöőúüű\-]{0,60}"
    # "Ft" without a trailing dot: "1 250 000 Ft." is usually a sentence end.
    _CUR = (r"Ft|ft|HUF|forint(?:ot|ra|ról|tal|nak|ért|ba|ban|ból|ig|on|hoz|tól|nál)?|EUR|"
            r"euró(?:t|ra|ról|val|nak|ért|ba|ban|ból|ig|n|hoz|tól|nál)?|€|USD|\$|CHF|GBP|£")
    PATTERNS = (
        Pattern(
            "amount_currency",
            # up to two line breaks before the currency: OCR splits "3 000 000\n\nFt"
            NB + rf"(?P<value>(?:{_NUM})(?:,-)?(?:\s?|\n\s*\n?)(?:(?:ezer|millió|milliárd|mrd\.?|e\.?|E|M|m)\s?)?(?:{_CUR}))(?![\w])",
            0.55,
        ),
        Pattern("currency_amount", rf"(?<![\w])(?P<value>(?:€|\$|£|EUR|HUF|USD|CHF)\s?(?:{_NUM}))" + NA, 0.55),
        Pattern("words_currency", rf"(?<![\w])(?P<value>{_WORDS}\s+(?:forint\w*|Ft|euró\w*|EUR|HUF))(?![\w])", 0.5),
    )
