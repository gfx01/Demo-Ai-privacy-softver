"""Opt-in "maximum" detection level (needs the analyzer venv and the lexicon)."""

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("presidio_analyzer")
pytestmark = pytest.mark.analyzer

from privacy_analyzer.engine import PrivacyEngine  # noqa: E402

LEXICON = Path(__file__).resolve().parents[2] / "models" / "lexicon"


@pytest.fixture(scope="module")
def engine():
    if not (LEXICON / "word_forms.txt").exists():
        pytest.skip("run: python services/analyzer/fetch_lexicon.py models/lexicon")
    os.environ.setdefault("ANALYZER_LEXICON_DIR", str(LEXICON))
    eng = PrivacyEngine(languages=["hu"], hu_ner="none", lexicon_dir=str(LEXICON))
    assert eng.maximum is not None
    return eng


def masked(engine, text, detection="maximum"):
    return [text[r.start : r.end] for r in engine.analyze(text, "hu", detection=detection)]


def covered(engine, text, part, detection="maximum"):
    i = text.index(part)
    return any(r.start <= i and i + len(part) <= r.end
               for r in engine.analyze(text, "hu", detection=detection))


@pytest.mark.parametrize("text, part", [
    ("Holnap a Zebrafa Studióval egyeztetünk.", "Zebrafa"),          # unknown proper noun inside a sentence
    ("Holnap a Zebrafa Studióval egyeztetünk.", "Studióval"),        # the rest of the capitalised run
    ("Tegnap beszéltem Krizsovánszkyval a dologról.", "Krizsovánszkyval"),
    ("Szia Dóri! Halasi Tamás bevallása még nincs kész.", "Halasi"),  # -i surname before a found given name
    ("Az ügyfél az XKR rendszerben szerepel.", "XKR"),                 # acronym
    ("A rendelésszám TQ-88412/B, kérlek nézd meg.", "TQ-88412/B"),     # letter-digit code
    ("Az ügyszám 4471-3/2026, a határidő péntek.", "4471-3/2026"),     # long number
    ("Született 1987.03.14., Pécsett.", "1987.03.14"),                 # date of a past year
    ("kérdezd meg zsuzsit, ő tudja", "zsuzsit"),                      # lower-case nickname
    ("Krizsovánszky szerint jó. Tegnap Krizsovánszky is ott volt.", "Krizsovánszky"),
])
def test_maximum_masks(engine, text, part):
    assert covered(engine, text, part), masked(engine, text)


@pytest.mark.parametrize("text, part", [
    ("Holnap a Zebrafa Studióval egyeztetünk.", "Zebrafa"),
    ("A rendelésszám TQ-88412/B, kérlek nézd meg.", "TQ-88412/B"),
])
def test_standard_does_not(engine, text, part):
    """The same cases are not masked by the standard level (what maximum adds)."""
    assert not covered(engine, text, part, detection="standard")


@pytest.mark.parametrize("text, part", [
    ("Kérem, hogy Ön is írja alá a szerződést.", "Ön"),                # polite pronoun
    ("A Megrendelő a teljesítést 8 napon belül igazolja.", "Megrendelő"),  # contract party
    ("Holnap 10-kor tartjuk a megbeszélést.", "Holnap"),              # sentence start, ordinary word
    ("Holnap 10-kor tartjuk a megbeszélést.", "10-kor"),
    ("A TAJ-száma és az SMS-t is küldd el.", "TAJ-száma"),            # generic acronym + ending
    ("A 2026.11.03-i határidőt tartjuk.", "2026.11.03"),              # current date is not a birth date
    ("Az irodánkban átutalni nem lehet, a számlázóprogramot frissítjük.", "irodánkban"),
    ("Az irodánkban átutalni nem lehet, a számlázóprogramot frissítjük.", "átutalni"),
    ("Az irodánkban átutalni nem lehet, a számlázóprogramot frissítjük.", "számlázóprogramot"),
    ("Tisztelt Ügyfelünk! A XIV. kerületben lévő irodánk zárva lesz.", "Tisztelt"),
])
def test_maximum_keeps_ordinary_words(engine, text, part):
    assert not covered(engine, text, part), masked(engine, text)


def test_standard_results_unchanged(engine):
    """maximum only adds: every standard span is still there."""
    text = "Kovács Péter (kovacs.peter@pelda.example, +36 30 000 1234) a Zebrafa Kft.-nél dolgozik Szegeden."
    std = {(r.entity_type, r.start, r.end) for r in engine.analyze(text, "hu")}
    mx = {(r.entity_type, r.start, r.end) for r in engine.analyze(text, "hu", detection="maximum")}
    assert std <= mx


def test_unknown_level_rejected(engine):
    with pytest.raises(ValueError):
        engine.analyze("szöveg", "hu", detection="paranoid")


def test_mask_roundtrip_placeholders(engine):
    res = engine.mask(["Holnap a Zebrafa Studióval egyeztetünk, a kód TQ-88412/B."], "hu", "bracket",
                      detection="maximum")
    out = res.masked_texts[0]
    assert "Zebrafa" not in out and "TQ-88412" not in out
    assert "[NAME_1]-vel" in out and "[CODE_1]" in out


def test_maximum_long_input_is_linear(engine):
    text = ("Holnap a Zebrafa Studióval és Krizsovánszkyval egyeztetünk a TQ-88412/B ügyben, 4471-3/2026. " * 400)
    started = time.perf_counter()
    engine.analyze(text, "hu", detection="maximum")
    assert time.perf_counter() - started < 20
