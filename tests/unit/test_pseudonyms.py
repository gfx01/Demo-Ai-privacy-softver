import random

import pytest

from privacy_hub.masking import MaskingSession, Span, StreamingRestorer, build_restorer
from privacy_hub.masking.hungarian import HungarianMorphology, inflect
from privacy_hub.masking.pseudonyms import FEMALE, MALE, SURNAMES, PseudonymStrategy

LEX = {"Kovács", "Péter", "Szabó", "Anna", "Tóth", "Éva", "Nagy", "Gábor"}
GENDER = {"Péter": "male", "Gábor": "male", "Anna": "female", "Éva": "female"}


def session(text_spans, key=b"k" * 32):
    strategy = PseudonymStrategy(gender_of=GENDER.get, key=key)
    texts = [t for t, _ in text_spans]
    s = MaskingSession(strategy, HungarianMorphology(lexicon=LEX), existing_texts=texts, language="hu")
    return [s.apply(t, spans) for t, spans in text_spans], s


def test_pools_follow_regular_stem_rule():
    for name in SURNAMES + MALE + FEMALE:
        assert not name.endswith(("a", "e")), name
        assert not name.lower().endswith(("cs", "sz", "zs", "gy", "ny", "ly", "ty", "th", "cz", "ch")), name
    assert all(len(s) >= 5 for s in SURNAMES)
    assert len(set(SURNAMES)) == len(SURNAMES)


def test_pseudonym_is_inflected_like_the_original_and_keeps_gender():
    text = "Beszéltem Kovács Péterrel és Szabó Annának írtam."
    (masked,), s = session([(text, [Span("PERSON", 10, 25), Span("PERSON", 29, 42)])])
    p1, p2 = s.mapping.entries
    assert p1.original == "Kovács Péter" and p2.original == "Szabó Anna"
    assert p1.parts[1][0] in MALE and p2.parts[1][0] in FEMALE
    assert inflect(p1.replacement, "INS") in masked and inflect(p2.replacement, "DAT") in masked
    assert "Kovács" not in masked and "Anna" not in masked


def test_deterministic_for_same_key_and_different_for_other_key():
    t = [("Kovács Péter jön.", [Span("PERSON", 0, 12)])]
    a = session(t, key=b"a" * 32)[1].mapping.entries[0].replacement
    assert a == session(t, key=b"a" * 32)[1].mapping.entries[0].replacement
    others = {session(t, key=bytes([i]) * 32)[1].mapping.entries[0].replacement for i in range(20)}
    assert len(others) > 5


def test_pseudonym_never_collides_with_input_text():
    # The input mentions every pool surname; the strategy must avoid them all.
    filler = " ".join(SURNAMES[:-1])
    text = f"Kovács Péter. {filler}"
    (_,), s = session([(text, [Span("PERSON", 0, 12)])])
    fake_surname = s.mapping.entries[0].parts[0][0]
    assert fake_surname not in filler


@pytest.mark.parametrize("case", ["NOM", "ACC", "DAT", "INS", "SUB", "DEL", "ADE", "ABL", "ALL", "CAU", "ESS"])
def test_restore_full_and_partial_mentions_in_any_case(case):
    text = "Kovács Péter és Szabó Anna."
    (_,), s = session([(text, [Span("PERSON", 0, 12), Span("PERSON", 16, 26)])])
    e1, e2 = s.mapping.entries
    fake_surname, fake_given = e1.parts[0][0], e1.parts[1][0]
    reply = f"{inflect(e1.replacement, case)}, {inflect(fake_given, case)}, {fake_surname} úr, {inflect(e2.replacement, case)}."
    out = build_restorer(s.mapping).restore(reply)
    assert out == f"{inflect('Kovács Péter', case)}, {inflect('Péter', case)}, Kovács úr, {inflect('Szabó Anna', case)}."


def test_name_endings_and_unrelated_words():
    (_,), s = session([("Kovács Péter", [Span("PERSON", 0, 12)])])
    fake_surname = s.mapping.entries[0].parts[0][0]
    r = build_restorer(s.mapping)
    assert r.restore(f"{fake_surname}né") == "Kovácsné"
    assert r.restore(f"{fake_surname}ék") == "Kovácsék"
    # a longer word that merely starts with the fake surname is not touched
    assert r.restore(f"{fake_surname}ságiak") == f"{fake_surname}ságiak"


def test_streaming_equals_batch_for_pseudonyms():
    text = "Kovács Péter a Zöldmező Kft. ügyvezetője, e-mail: kp@example.com"
    spans = [Span("PERSON", 0, 12), Span("HU_COMPANY", 15, 28), Span("EMAIL_ADDRESS", 50, 64)]
    (_,), s = session([(text, spans)])
    e_person, e_company, e_email = s.mapping.entries
    reply = (f"Írtam {inflect(e_person.replacement, 'DAT')} és a {inflect(e_company.replacement, 'INS')} "
             f"kapcsolatban; válasz: {e_email.replacement}. {inflect(e_person.parts[1][0], 'ACC')} holnap hívom.")
    expected = build_restorer(s.mapping).restore(reply)
    assert "Kovács Péternek" in expected and "Zöldmező Kft.-vel" in expected and "kp@example.com" in expected
    rng = random.Random(3)
    for _ in range(200):
        sr = StreamingRestorer(build_restorer(s.mapping))
        out, i = [], 0
        while i < len(reply):
            n = rng.randint(1, 5)
            out.append(sr.feed(reply[i : i + n]))
            i += n
        out.append(sr.flush())
        assert "".join(out) == expected


def test_bracket_fallback_types_inside_pseudonym_mode():
    text = "Budapesten 1 250 000 Ft-ot fizetett."
    (masked,), s = session([(text, [Span("LOCATION", 0, 10), Span("MONEY", 11, 23)])])
    assert "[LOCATION_1]" in masked and "[AMOUNT_1]" in masked
    assert build_restorer(s.mapping).restore(masked) == text
