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


@pytest.mark.parametrize("text, expected", [
    # foreign names: particles, salutation cue, foreign surname shape, repeated cued surname
    ("Írj a holland partnernek, Jeroen de Vriesnek a számláról.", ("PERSON", "Jeroen de Vriesnek")),
    ("Kedves Liam O'Connor! Megerősítjük a foglalást.", ("PERSON", "Liam O'Connor")),
    ("a partnernek: „Kedves Sanne! Köszönöm", ("PERSON", "Sanne")),
    ("Tisztelt Doyle Úr! Lánya, Aoife Doyle jelentkezett.", ("PERSON", "Aoife Doyle")),
    # day name as surname, possessive -é, legal case parties, old spelling in lower case
    ("Kérdés esetén kollégámat, Péntek Ritát keresse.", ("PERSON", "Péntek Ritát")),
    ("Megnéztem a papírokat, Szabó Gáborét is.", ("PERSON", "Szabó Gáborét")),
    ("Csatolom a Pálfi kontra Vasvári ügy iratait.", ("PERSON", "Pálfi")),
    ("holnap váradynál van hely 11-kor", ("PERSON", "váradynál")),
    ("Szabó Gergelyt elbocsátották. Szabó szerint a felmondás jogszerű.", ("PERSON", "Szabó")),
    ("hogy a „Gombos Ildikó” néven leadott rendelést", ("PERSON", "Gombos Ildikó")),
    # places: hyphenated geographic names, foreign cities, lower case in chat, streets
    ("a vacsora a Hajógyári-szigeten lesz", ("LOCATION", "Hajógyári-szigeten")),
    ("aki tavaly Stuttgartból költözött ide", ("LOCATION", "Stuttgartból")),
    ("azt mondják a kisteleken lévő raktárba", ("LOCATION", "kisteleken")),
    ("Eger, Széchenyi u., kedd 14:00", ("LOCATION", "Széchenyi u.")),
    ("be tudsz jönni a kálvin térre?", ("LOCATION", "kálvin térre")),
    # addresses: foreign, lower-case words in the street name, Roman floor / door, no accents
    ("Vevő: Weinhaus GmbH, 4020 Linz, Landstraße 41, Ausztria.", ("HU_ADDRESS", "4020 Linz, Landstraße 41")),
    ("székhely: 1117 Budapest, Október huszonharmadika utca 41. III/12, cégjegyzékszám",
     ("HU_ADDRESS", "1117 Budapest, Október huszonharmadika utca 41. III/12")),
    ("irodája: 4024 Debrecen, Piac utca 41. II/5, ahol", ("HU_ADDRESS", "4024 Debrecen, Piac utca 41. II/5")),
    ("a cim jo: 4031 Debrecen, Kishegyesi ut 112, kerlek", ("HU_ADDRESS", "4031 Debrecen, Kishegyesi ut 112")),
    # money shorthand, organisations without a legal form, foreign phone numbers
    ("az előlegszámlát 150e Ft-tal csökkentettük", ("MONEY", "150e Ft")),
    ("a keret 2,5 mrd Ft", ("MONEY", "2,5 mrd Ft")),
    ("a Brightwater Health biztosító fizet", ("ORGANIZATION", "Brightwater Health")),
    ("a Kék Út Autóbérlőnél foglalt", ("ORGANIZATION", "Kék Út Autóbérlőnél")),
    ("tárgyalás lesz a budai központi kerületi bíróságon", ("ORGANIZATION", "budai központi kerületi bíróságon")),
    ("a bécsi partner: +43 1 234 5678", ("PHONE_NUMBER", "+43 1 234 5678")),
])
def test_round3_positives(engine, text, expected):
    assert expected in found(engine, text)


@pytest.mark.parametrize("text", [
    "Emlékeztető: pénteken 14:00-tól megbeszélés.",
    "előbb meg kellene hallgatni.",
    "jármű: Škoda Octavia Combi",
    "a 3.P.21.077/2025. szám alatt folyamatban lévő ügy",
    "Visszahozatal: 2026.10.12. 21:40",
    "a számla sorszáma SZ-2026/001184",
    "érvénytelen azonosító (ref: 7745012938)",
    "Minden rendben, Most jöttem haza.",
    "a budai irodába megyek, a szegedi raktárba",
    "ezen úton megyünk, a hosszú úton",
    "a partnerprogram és az Excel-táblázat",
])
def test_round3_negatives(engine, text):
    assert found(engine, text) == set()


@pytest.mark.parametrize("text, span", [
    ("Thomas van Budapesten dolgozik.", "Thomas van Budapesten"),  # a place after "van" (= "is")
    ("Anna de Péter nem jött.", "Anna de Péter"),  # "de" (= "but") between two given names
])
def test_round3_particles_need_a_surname(engine, text, span):
    assert ("PERSON", span) not in found(engine, text)


@pytest.mark.parametrize("text, expected", [
    # OCR damage seen on rendered test documents (Tesseract): "@", "+", digits, letters
    ("küldjék a konyvelesOrabaszamviteli. example címre", ("EMAIL_ADDRESS", "konyvelesOrabaszamviteli. example")),
    ("Bakos Lilla (e-mail: bakos.lillagohajnal-zrt.example, tel.:", ("EMAIL_ADDRESS", "bakos.lillagohajnal-zrt.example")),
    ("A választ a gal.viktoria orozmaring-rendezveny.example címre", ("EMAIL_ADDRESS", "gal.viktoria orozmaring-rendezveny.example")),
    ("írj a kovacs.anna @ ceg.hu címre", ("EMAIL_ADDRESS", "kovacs.anna @ ceg.hu")),
    ("küldd át: d.olah@hangfalbtexample, köszi", ("EMAIL_ADDRESS", "d.olah@hangfalbtexample")),
    ("hívja vissza a 136 70 000 6630 számon", ("PHONE_NUMBER", "136 70 000 6630")),
    ("tel.: --36 20 000 3019, köszönöm", ("PHONE_NUMBER", "--36 20 000 3019")),
    ("Győr, tel.: 4-36 96 000 307", ("PHONE_NUMBER", "4-36 96 000 307")),
    ("átutalással a HUO8 0278 8177 1599 9869 5784 5126 számlára", ("IBAN_CODE", "HUO8 0278 8177 1599 9869 5784 5126")),
    ("a 955031851-36678652-30484466 számú bankszámlára", ("HU_BANK_ACCOUNT", "955031851-36678652-30484466")),
    ("okmánnyal (szig. SZ.: 216675]JA)", ("HU_ID_CARD", "216675]JA")),
    ("Fehér Kristóf, 34 éves, sziil.: 1992.03.17., foglalkozása", ("HU_BIRTH_DATE", "1992.03.17.")),
    ("Kun Bamabástól pedig 160 000 ft", ("PERSON", "Kun Bamabástól")),
    ("Kun Bamabástól pedig 160 000 ft", ("MONEY", "160 000 ft")),
    ("940 521 096, Nagy Lérincnél 2026. október 12-én", ("PERSON", "Nagy Lérincnél")),
    ("peti, bocs h zavarlak, lukacsék végrehajtásánál", ("PERSON", "lukacsék")),
    ("aláírás: Csongrád-Mag K ft., pénzügy", ("HU_COMPANY", "Csongrád-Mag K ft.")),
])
def test_ocr_tolerant_positives(engine, text, expected):
    assert expected in found(engine, text)


@pytest.mark.parametrize("text", [
    "nem kernek tobbet, kellene, szeretnek, mennek, lesznek, vannak, lehetne, kerek egy kavet",
    "a www.google.com oldal és a portal.nav.gov.hu",
    "az e-mailt a ugyfelkapu.gov.hu felületen kell beküldeni",
    "A termékkód 12345678-87654321, nem számlaszám.",
])
def test_ocr_tolerant_negatives(engine, text):
    assert found(engine, text) == set()


@pytest.mark.parametrize("text, expected", [
    ("amit Ioana Mureșannak küldünk", ("PERSON", "Ioana Mureșannak")),  # Latin Extended-B (ș)
    ("este Gödöllőn, a Szabadság tér melletti parkolóban", ("LOCATION", "Szabadság tér")),
    ("a Halasi uton levo telephelyen", ("LOCATION", "Halasi uton")),
    ("esedékes. Ajka, 2026. október 12., dr. Fülöp J.", ("LOCATION", "Ajka")),
    ("Kontroll: dr. Fülöp J. rendelésén", ("PERSON", "Fülöp J.")),
    ("Platán Idősek Otthona – felvételi adatlap", ("ORGANIZATION", "Platán Idősek Otthona")),
])
def test_round3b_positives(engine, text, expected):
    assert expected in found(engine, text)


@pytest.mark.parametrize("text", [
    "Tisztelt Adatvédelmi Tisztviselő! Kedves Pénzügyi Osztály!",
    "emlékeztető a szülőknek: a Csillag és a Virág csoport közös farsangja",
    "Az Arany szintű támogatás csak hétköznap érhető el.",
    "a kulcs nala van, holnap visszahozza",
    "Hosszú út áll előttünk.",
])
def test_round3b_negatives(engine, text):
    assert found(engine, text) == set()


@pytest.mark.parametrize("unit", ["12345678-", "a-", "A-", "né", "ő", "I. ", "Ab u. ", "1/", "a0", "Aa Aa ", "06 "])
def test_no_catastrophic_backtracking(engine, unit):
    """Untrusted input: a long run of one token must stay roughly linear.
    Before the fixes, "12345678-" x 60 froze the company regex (combinatorial
    hyphen parses) and a 3,000-character "nénéné..." took 14 s in the word
    analysis; Python's re holds the GIL, so one request froze the analyzer."""
    import time

    text = unit * (20000 // len(unit))
    t = time.perf_counter()
    engine.mask([text], "hu", "bracket")
    assert time.perf_counter() - t < 5, f"{unit!r} x {len(text) // len(unit)} took too long"


def test_fast_remove_duplicates_matches_presidio():
    """The O(n log n) replacement keeps exactly what Presidio's pairwise version keeps."""
    from presidio_analyzer import RecognizerResult

    from privacy_analyzer.engine import PRESIDIO_REMOVE_DUPLICATES, fast_remove_duplicates

    rng = random.Random(7)
    for _ in range(300):
        results = []
        for _ in range(rng.randint(0, 40)):
            start = rng.randint(0, 30)
            results.append(RecognizerResult(rng.choice(["PERSON", "LOCATION", "EMAIL_ADDRESS"]), start,
                                            start + rng.randint(1, 8), rng.choice([0, 0.3, 0.5, 0.5, 0.85, 1.0])))
        results += rng.sample(results, k=min(5, len(results)))  # exact duplicates
        key = lambda r: (r.start, r.end, r.entity_type, r.score)  # noqa: E731
        assert sorted(map(key, fast_remove_duplicates(results))) == sorted(map(key, PRESIDIO_REMOVE_DUPLICATES(results)))


def test_replace_spans_matches_presidio_anonymizer(engine):
    """Linear replacement = Presidio AnonymizerEngine with a custom operator:
    same text and the same order of replacement calls (placeholder numbering)."""
    import json as _json

    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    from privacy_analyzer.engine import replace_spans

    root = Path(__file__).resolve().parents[2] / "eval" / "data"
    texts = []
    for name in ("hu_testset_dev.jsonl", "hu_testset_heldout.jsonl"):
        path = root / name
        if path.exists():
            texts += [_json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not texts:
        pytest.skip("test sets not built (make eval)")
    texts.append("Kovács Péter és Szabó Anna, Nagy  Éva\tTóth Gábor")  # spaces vs other whitespace between entities
    anonymizer = AnonymizerEngine()
    for text in texts:
        results = engine.analyze(text, "hu")
        if not results:
            continue
        ours, theirs = [], []

        def recorder(log):
            return lambda etype, surface: log.append((etype, surface)) or f"<{etype}:{len(log)}>"

        mine = replace_spans(text, results, recorder(ours))
        rec = recorder(theirs)
        ops = {t: OperatorConfig("custom", {"lambda": (lambda s, t=t: rec(t, s))}) for t in {r.entity_type for r in results}}
        reference = anonymizer.anonymize(text=text, analyzer_results=results, operators=ops).text
        assert mine == reference
        assert ours == theirs
