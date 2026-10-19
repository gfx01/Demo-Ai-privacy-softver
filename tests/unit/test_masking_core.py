import random

import pytest

from privacy_hub.masking import MaskingSession, Span, StreamingRestorer, build_restorer
from privacy_hub.masking.model import Mapping


def _mask(text: str, spans: list[Span], strategy: str = "bracket"):
    session = MaskingSession(strategy, existing_texts=[text], language="en")
    return session.apply(text, spans), session


def test_bracket_masking_is_consistent_and_ordered():
    text = "John Smith emailed jane@example.com. Later John Smith called."
    spans = [Span("PERSON", 0, 10), Span("EMAIL_ADDRESS", 19, 35), Span("PERSON", 43, 53)]
    masked, session = _mask(text, spans)
    assert masked == "[PERSON_1] emailed [EMAIL_1]. Later [PERSON_1] called."
    assert session.entity_counts == {"PERSON": 2, "EMAIL_ADDRESS": 1}
    assert [e.original for e in session.mapping.entries] == ["John Smith", "jane@example.com"]


def test_existing_tokens_in_input_are_not_reused():
    text = "Template uses [PERSON_1]; real name is John Smith."
    masked, session = _mask(text, [Span("PERSON", 39, 49)])
    assert masked == "Template uses [PERSON_1]; real name is [PERSON_2]."
    restorer = build_restorer(session.mapping)
    # The user's own literal token must survive restoration untouched.
    assert restorer.restore(masked) == text


def test_overlaps_prefer_higher_score_then_longer():
    text = "Contact John Smith Jr today"
    spans = [Span("PERSON", 8, 18, 0.6), Span("PERSON", 8, 21, 0.6), Span("LOCATION", 13, 18, 0.9)]
    masked, _ = _mask(text, spans)
    assert masked == "Contact John [LOCATION_1] Jr today"


@pytest.mark.parametrize(
    "variant, expected",
    [
        ("[PERSON_1]", "John Smith"),
        ("[person_1]", "John Smith"),
        ("[PERSON 1]", "John Smith"),
        ("[ PERSON_1 ]", "John Smith"),
        ("[PERSON-1]", "John Smith"),
        ("PERSON_1", "John Smith"),
        ("**[PERSON_1]**", "**John Smith**"),
        ("([PERSON_1])", "(John Smith)"),
    ],
)
def test_restore_tolerates_variants(variant, expected):
    _, session = _mask("John Smith", [Span("PERSON", 0, 10)])
    out = build_restorer(session.mapping).restore(f"Hello {variant}, how are you?")
    assert out == f"Hello {expected}, how are you?"


def test_unknown_or_longer_numbers_are_not_restored():
    _, session = _mask("John Smith", [Span("PERSON", 0, 10)])
    r = build_restorer(session.mapping)
    assert r.restore("[PERSON_12] and [PERSON_2] and PERSON_10") == "[PERSON_12] and [PERSON_2] and PERSON_10"


def test_lowercase_bare_token_is_not_restored():
    # "person_1" in prose is too ambiguous without brackets.
    _, session = _mask("John Smith", [Span("PERSON", 0, 10)])
    assert build_restorer(session.mapping).restore("see person_1") == "see person_1"


def test_null_morphology_reattaches_suffix_verbatim():
    _, session = _mask("John Smith", [Span("PERSON", 0, 10)])
    assert build_restorer(session.mapping).restore("[PERSON_1]-based") == "John Smith-based"


def _stream(restorer_mapping: Mapping, text: str, sizes: list[int]) -> str:
    sr = StreamingRestorer(build_restorer(restorer_mapping))
    out, pos = [], 0
    for size in sizes:
        out.append(sr.feed(text[pos : pos + size]))
        pos += size
    out.append(sr.feed(text[pos:]))
    out.append(sr.flush())
    return "".join(out)


def test_streaming_restore_matches_batch_for_random_chunkings():
    text = "John Smith wrote to jane@example.com about John Smith's contract."
    spans = [Span("PERSON", 0, 10), Span("EMAIL_ADDRESS", 20, 36), Span("PERSON", 43, 53)]
    masked, session = _mask(text, spans)
    model_output = f"Summary: {masked} Also [PERSON_1] and [EMAIL_1]."
    expected = build_restorer(session.mapping).restore(model_output)
    rng = random.Random(7)
    for _ in range(300):
        sizes = [rng.randint(1, 6) for _ in range(len(model_output))]
        assert _stream(session.mapping, model_output, sizes) == expected
    # Character-by-character, the hardest split.
    assert _stream(session.mapping, model_output, [1] * len(model_output)) == expected


def test_streaming_holds_back_only_a_bounded_tail():
    _, session = _mask("John Smith", [Span("PERSON", 0, 10)])
    restorer = build_restorer(session.mapping)
    sr = StreamingRestorer(restorer)
    emitted = sr.feed("x" * 500)
    assert len(emitted) >= 500 - restorer.max_match_len
    assert len(sr.pending) <= restorer.max_match_len


def test_mapping_roundtrip():
    _, session = _mask("John Smith", [Span("PERSON", 0, 10)])
    m = session.mapping
    assert Mapping.from_dict(m.to_dict()) == m
    with pytest.raises(ValueError):
        Mapping.from_dict({**m.to_dict(), "version": 99})
