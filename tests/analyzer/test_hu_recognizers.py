"""Hungarian recognizers (needs the analyzer venv: presidio + spaCy)."""

import os
import random
from pathlib import Path

import pytest

pytest.importorskip("presidio_analyzer")
pytestmark = pytest.mark.analyzer

from privacy_analyzer.engine import PrivacyEngine  # noqa: E402

from privacy_hub import hu_ids  # noqa: E402

LEXICON = Path(__file__).resolve().parents[2] / "models" / "lexicon"


@pytest.fixture(scope="module")
def engine():
    if not (LEXICON / "given_male.txt").exists():
        pytest.skip("run: python services/analyzer/fetch_lexicon.py models/lexicon")
    os.environ.setdefault("ANALYZER_LEXICON_DIR", str(LEXICON))
    return PrivacyEngine(languages=["hu"], hu_ner="none", lexicon_dir=str(LEXICON))


def found(engine, text):
    return {(r.entity_type, text[r.start : r.end]) for r in engine.analyze(text, "hu")}


RNG = random.Random(2024)
TAJ = hu_ids.gen_taj(RNG)
TAX = hu_ids.gen_tax_id(RNG)
ADOSZAM = hu_ids.gen_adoszam(RNG)
ACCOUNT = hu_ids.gen_bank_account(RNG, 3)
IBAN = hu_ids.gen_iban(RNG)


@pytest.mark.parametrize("text, expected", [
    (f"A beteg TAJ-száma: {TAJ}.", ("HU_TAJ", TAJ)),
    (f"Adóazonosító jel: {TAX}", ("HU_TAX_ID", TAX)),
    (f"A Zöldmező Kft. adószáma {ADOSZAM}.", ("HU_VAT_NUMBER", ADOSZAM)),
    (f"Kérjük, utalja a {ACCOUNT} számlaszámra.", ("HU_BANK_ACCOUNT", ACCOUNT)),
    (f"IBAN: {IBAN}", ("IBAN_CODE", IBAN)),
    ("Cégjegyzékszám: Cg. 01-09-123456", ("HU_COMPANY_REG_NUMBER", "Cg. 01-09-123456")),
    ("Személyi igazolvány száma: 123456AB", ("HU_ID_CARD", "123456AB")),
    ("A gépkocsi rendszáma ABC-123.", ("HU_LICENSE_PLATE", "ABC-123")),
    ("Az új rendszám: AA BC-123", ("HU_LICENSE_PLATE", "AA BC-123")),
    ("Hívj a +36 30 000 4567 számon!", ("PHONE_NUMBER", "+36 30 000 4567")),
    ("Tel.: 06-30/000-4567", ("PHONE_NUMBER", "06-30/000-4567")),
    ("Iroda: (06 1) 234 5678", ("PHONE_NUMBER", "(06 1) 234 5678")),
    ("Szegedi iroda: +36 62 000 456", ("PHONE_NUMBER", "+36 62 000 456")),
    ("Írj a kovacs.peter@example.com címre.", ("EMAIL_ADDRESS", "kovacs.peter@example.com")),
    ("Lakcím: 1052 Budapest, Váci utca 12. 3. em. 4.", ("HU_ADDRESS", "1052 Budapest, Váci utca 12. 3. em. 4.")),
    ("Székhely: 6720 Szeged, Kárász u. 5.", ("HU_ADDRESS", "6720 Szeged, Kárász u. 5.")),
    ("Szerződtünk a Zöldmező Építő Kft.-vel.", ("HU_COMPANY", "Zöldmező Építő Kft.")),
    ("A Fehér és Társa Bt.-től kaptuk.", ("HU_COMPANY", "Fehér és Társa Bt.")),
    ("Az ára 1 250 000 Ft volt.", ("MONEY", "1 250 000 Ft")),
    ("Összesen 3 500 EUR.", ("MONEY", "3 500 EUR")),
    ("Beszéltem Kovács Péterrel.", ("PERSON", "Kovács Péterrel")),
    ("Add át Szabó Annának!", ("PERSON", "Szabó Annának")),
    ("Kovácsné Tóth Éva holnap jön.", ("PERSON", "Kovácsné Tóth Éva")),
    ("Kedves Péter!", ("PERSON", "Péter")),
    ("Szül.: 1985. március 12.", ("HU_BIRTH_DATE", "1985. március 12.")),
])
def test_positive(engine, text, expected):
    assert expected in found(engine, text)


@pytest.mark.parametrize("text", [
    "A rendelés száma 123 456 780, kérem hivatkozzon rá.",  # invalid TAJ checksum, no context
    "A Ptk. 6:142. § alapján a kár megtérítendő.",
    "Holnap reggel nyolckor kezdünk.",
    "Egy Kft.-t szeretnék alapítani.",
    "A Petőfi Sándor utcában lakik.",
    "Rózsa illata töltötte be a szobát.",
    "A termékkód 12345678-87654321, nem számlaszám.",  # invalid giro checksum
])
def test_negative(engine, text):
    got = found(engine, text)
    assert not got, got


def test_invalid_taj_with_context_is_kept_with_low_score(engine):
    text = "TAJ szám: 123 456 780"
    results = [r for r in engine.analyze(text, "hu") if r.entity_type == "HU_TAJ"]
    assert results and results[0].score < 0.9


def test_suffix_after_identifier_stays_outside(engine):
    text = f"A {ADOSZAM}-nek megfelelő cég"
    assert ("HU_VAT_NUMBER", ADOSZAM) in found(engine, text)


def test_mask_hungarian_bracket(engine):
    res = engine.mask(["Beszéltem Kovács Péterrel és Szabó Annának írtam."], "hu", "bracket")
    assert res.masked_texts[0] == "Beszéltem [PERSON_1]-vel és [PERSON_2]-nek írtam."
    assert [e.original for e in res.mapping.entries] == ["Kovács Péter", "Szabó Anna"]


def test_decomposed_accents_and_invisible_characters_are_normalised(engine):
    import unicodedata

    text = unicodedata.normalize("NFD", "Kovács Péternek írtam.").replace("Kov", "Ko\u200bv")
    assert "\u0301" in text and "\u200b" in text
    result = engine.mask([text], "hu", "bracket")
    assert "Kov" not in result.masked_texts[0] and "ter" not in result.masked_texts[0]
    assert result.masked_texts[0].startswith("[PERSON_1]")


@pytest.mark.parametrize("address", ["titkarsag@ceg.local", "kp@corp.internal", "anna.kovacs@iroda.example", "x@példa.hu"])
def test_email_on_any_top_level_domain(engine, address):
    assert ("EMAIL_ADDRESS", address) in found(engine, f"Írj a {address} címre.")


@pytest.mark.parametrize("invisible", ["\u200e", "\u202c", "\u2066", "\ufe0f", "\U000e0041", "\u034f", "\u2063", "\u00ad", "\u3164"])
def test_invisible_characters_cannot_split_a_name(engine, invisible):
    result = engine.mask([f"Írj Kov{invisible}ács Péternek a tárgyalásról."], "hu", "bracket")
    assert result.masked_texts[0].startswith("Írj [PERSON_1]"), repr(result.masked_texts[0])
