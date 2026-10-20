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
    needed = ("given_male.txt", "surnames.txt", "settlements.txt", "common_words.txt", "countries.txt")
    if not all((LEXICON / f).exists() for f in needed):
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


@pytest.mark.parametrize("text, expected", [
    # birth dates: whole day, "születési" context, cue after the date
    ("ifj. Kerekes János (szül.: 1988.02.14.) eladó", ("HU_BIRTH_DATE", "1988.02.14.")),
    ("születési dátum: 1993. február 17. és", ("HU_BIRTH_DATE", "1993. február 17.")),
    ("a kérelmező 1991. augusztus 23. napján született Pécsen", ("HU_BIRTH_DATE", "1991. augusztus 23.")),
    # married / family forms, both vowel harmonies, unknown surnames
    ("Fodor Lászlónéhoz fordultunk.", ("PERSON", "Fodor Lászlónéhoz")),
    ("továbbítani Vargáéknak is", ("PERSON", "Vargáéknak")),
    ("a mellékletet Bartosnénak küldtem", ("PERSON", "Bartosnénak")),
    ("Holnap Erdősné hoz sütit.", ("PERSON", "Erdősné")),
    # cues: title or salutation before an unknown surname
    ("Tisztelt Mr. O'Brien!", ("PERSON", "O'Brien")),
    ("Tisztelt Bencze Úr!", ("PERSON", "Bencze")),
    ("szintén Dr. Kerekeshez fordulj", ("PERSON", "Kerekeshez")),
    ("a zárót küldd át dr. rozsnyainak", ("PERSON", "rozsnyainak")),
    ("Beteg: Kertész Péter, szül.: 1971.08.23.", ("PERSON", "Kertész Péter")),
    # chat style without accents, line break inside a name, Latin Extended-A
    ("nalunk szabo gabi intezi", ("PERSON", "szabo gabi")),
    ("segits pls: a kovacs petinek irj", ("PERSON", "kovacs petinek")),
    ("Küldjétek meg Szilágyi\nNórának.", ("PERSON", "Szilágyi\nNórának")),
    ("kilépési interjú: Ana Petrović.", ("PERSON", "Ana Petrović")),
    # repeated part of a name, foreign given name with an ending
    ("Thomas Becker jön, Beckerrel egyeztess.", ("PERSON", "Beckerrel")),
    ("Becker nem beszél magyarul: Becker Thomast hívd", ("PERSON", "Becker Thomast")),
    # address tails (floor / door)
    ("Lakcíme 7400 Kaposvár, Dózsa György utca 15. 2/7, TAJ", ("HU_ADDRESS", "7400 Kaposvár, Dózsa György utca 15. 2/7")),
    ("címe: 1054 Budapest, Báthory utca 20. I. emelet 4. Értesítjük", ("HU_ADDRESS", "1054 Budapest, Báthory utca 20. I. emelet 4.")),
    ("5000 Szolnok, Baross Gábor utca 27. fszt. 1/A szám alatt", ("HU_ADDRESS", "5000 Szolnok, Baross Gábor utca 27. fszt. 1/A")),
    # organisations: compound heads, lengthened head, foreign legal forms
    ("a Mecsek Utazási Irodától", ("ORGANIZATION", "Mecsek Utazási Irodától")),
    ("a Fővárosi Törvényszék Cégbíróságánál", ("ORGANIZATION", "Fővárosi Törvényszék Cégbíróságánál")),
    ("a Somogyi Vesecentrumba", ("ORGANIZATION", "Somogyi Vesecentrumba")),
    ("a Hornád Soft s.r.o. kéri", ("HU_COMPANY", "Hornád Soft s.r.o.")),
])
def test_round2_positives(engine, text, expected):
    assert expected in found(engine, text)


@pytest.mark.parametrize("text", [
    "Kedves Kollégák!",
    "Tisztelt Ügyfélszolgálat!",
    "A szerződés 2021. március 3. napján lépett hatályba.",
    "Számla kelte: 2023.04.12.",
    "Az Excel Bankkártya menüben kattints.",
    "Holnap Iskolába megyek.",
    "dr. med. univ.",
])
def test_round2_negatives(engine, text):
    assert found(engine, text) == set()


def test_street_named_after_a_person_is_a_location_not_a_person(engine):
    hits = found(engine, "A Petőfi Sándor utcában lakik.")
    assert ("LOCATION", "Petőfi Sándor utcában") in hits
    assert not any(t == "PERSON" for t, _ in hits)


@pytest.mark.parametrize("text, expected", [
    ("Szia Erika!", ("PERSON", "Erika")),
    ("Gt.: Farkas Imréné kérte", ("PERSON", "Farkas Imréné")),
    ("A 18 500 Ft. Barnáéknak szól", ("PERSON", "Barnáéknak")),
    ("tudnál írni varga leventének", ("PERSON", "varga leventének")),
    ("június 20-ra. Kertész Dorina tegnap", ("PERSON", "Kertész Dorina")),
    ("gyógytorna. K. Péternek szól", ("PERSON", "K. Péternek")),
    ("megbízza a Rácz és Bodnár Ügyvédi Irodát", ("ORGANIZATION", "Rácz és Bodnár Ügyvédi Irodát")),
    ("Üdv: Gergő (Halmos és Társai)", ("HU_COMPANY", "Halmos és Társai")),
    ("a Vörösmarty utcai rendelőbe", ("LOCATION", "Vörösmarty utcai")),
    ("jövő héten Budapestre jön", ("LOCATION", "Budapestre")),
    ("a Németországba költözik", ("LOCATION", "Németországba")),
    ("az Egyesült Államokban él", ("LOCATION", "Egyesült Államokban")),
    ("Pécsett lakik a nővére.", ("LOCATION", "Pécsett")),
    ("A Bitkert Informatikai Zrt. ajánlata jó, a Bitkerttel szerződünk.", ("HU_COMPANY", "Bitkerttel")),
])
def test_round2_lexicon_positives(engine, text, expected):
    assert expected in found(engine, text)


@pytest.mark.parametrize("text", [
    "a belső Fehér Könyvünk egyik fejezete",
    "mert ő holnaptól szabin van",
    "mérlegeljék a koponya-MR szükségességét.",
    "Hosszú út áll előttünk.",
    "Hatvan perc múlva indulunk.",
    "a rózsa és a virág szép",
])
def test_round2_lexicon_negatives(engine, text):
    assert found(engine, text) == set()
