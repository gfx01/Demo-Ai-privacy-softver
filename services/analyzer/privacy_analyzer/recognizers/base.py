"""Regex recognizer with checksum validation and Hungarian context words.

Presidio's built-in context enhancement matches context words against spaCy
lemmas. Hungarian context words are inflected ("TAJ-számát", "adószámú",
"bankszámlaszámra") and some NER backends provide no lemmas at all, so this
base class checks a context *regex* in a character window around the match.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult

# Boundaries: no digit/letter directly before; no digit after. A hyphenated
# Hungarian suffix after the value ("12345678-1-42-nek") is allowed and stays
# outside the entity span.
NB = r"(?<![\w\-/.])"
NA = r"(?![\d])(?!\.\d)"


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: str
    score: float
    needs_context: bool = False


class HuRegexRecognizer(EntityRecognizer):
    ENTITY: str = ""
    PATTERNS: tuple[Pattern, ...] = ()
    CONTEXT: str | None = None  # regex, case-insensitive
    CONTEXT_BEFORE = 60
    CONTEXT_AFTER = 25
    CONTEXT_BOOST = 0.35
    REQUIRE_CONTEXT = False
    # If the checksum fails but context words are present, keep the match
    # with a low score (a mistyped TAJ is still a TAJ). For company-level
    # identifiers this is off to reduce false positives.
    ACCEPT_INVALID_WITH_CONTEXT = False
    FLAGS = re.UNICODE

    checksum: Callable[[str], bool] | None = None

    def __init__(self, supported_language: str = "hu") -> None:
        super().__init__(supported_entities=[self.ENTITY], supported_language=supported_language, name=type(self).__name__)
        self._compiled = [(p, re.compile(p.regex, self.FLAGS)) for p in self.PATTERNS]
        self._context = re.compile(self.CONTEXT, re.IGNORECASE | re.UNICODE) if self.CONTEXT else None

    def load(self) -> None:  # required by EntityRecognizer
        pass

    def has_context(self, text: str, start: int, end: int) -> bool:
        if self._context is None:
            return False
        window = text[max(0, start - self.CONTEXT_BEFORE) : start] + " " + text[end : end + self.CONTEXT_AFTER]
        return bool(self._context.search(window))

    def value_span(self, match: re.Match) -> tuple[int, int]:
        if "value" in match.re.groupindex and match.group("value") is not None:
            return match.span("value")
        return match.span()

    def validate(self, value: str) -> bool | None:
        """True/False if a verified check digit exists, None otherwise."""
        fn = type(self).checksum
        return None if fn is None else bool(fn(value))

    def analyze(self, text, entities, nlp_artifacts=None):  # noqa: ARG002
        results: list[RecognizerResult] = []
        seen: set[tuple[int, int]] = set()
        for pattern, rx in self._compiled:
            for m in rx.finditer(text):
                start, end = self.value_span(m)
                if (start, end) in seen:
                    continue
                value = text[start:end]
                ctx = self.has_context(text, start, end)
                if (self.REQUIRE_CONTEXT or pattern.needs_context) and not ctx:
                    continue
                valid = self.validate(value)
                score = pattern.score
                if valid is False:
                    if not (ctx and self.ACCEPT_INVALID_WITH_CONTEXT):
                        continue
                    score = 0.45
                elif valid is True:
                    score = max(score, 0.85)
                if ctx:
                    score = min(1.0, score + self.CONTEXT_BOOST)
                seen.add((start, end))
                explanation = AnalysisExplanation(
                    recognizer=self.name,
                    original_score=pattern.score,
                    pattern_name=pattern.name,
                    validation_result=valid,
                    textual_explanation=f"context={'yes' if ctx else 'no'}",
                )
                results.append(RecognizerResult(self.ENTITY, start, end, round(score, 3), explanation))
        return results
