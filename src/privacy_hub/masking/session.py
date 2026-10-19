"""Per-request masking session.

One session is used for all messages of one LLM request so that the same
original value gets the same placeholder everywhere in the conversation.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence

from .labels import label_for
from .model import Mapping, MappingEntry, MaskResult, Span
from .morphology import NO_INFLECTION, Morphology, NullMorphology
from .strategies import EXISTING_TOKEN_RE, Strategy, get_strategy


def normalise(value: str) -> str:
    """Key used to recognise repeated mentions of the same original value."""
    value = unicodedata.normalize("NFC", value)
    return " ".join(value.split()).casefold()


class MaskingSession:
    def __init__(
        self,
        strategy: Strategy | str,
        morphology: Morphology | None = None,
        existing_texts: Iterable[str] = (),
        language: str = "en",
    ) -> None:
        self.language = language
        self.strategy = get_strategy(strategy) if isinstance(strategy, str) else strategy
        self.morphology: Morphology = morphology or NullMorphology()
        self._split_morph: Morphology = self.morphology if self.strategy.splits_suffix else NullMorphology()
        self._entries: dict[tuple[str, str], MappingEntry] = {}
        self._ordered: list[MappingEntry] = []
        self._counters: Counter[str] = Counter()
        self._counts: Counter[str] = Counter()
        # Never reuse a token number that already occurs in the user's input,
        # otherwise restoration would rewrite text the user typed themselves.
        self._reserved: set[str] = set()
        self._avoid: set[str] = set()
        for text in existing_texts:
            for m in EXISTING_TOKEN_RE.finditer(text):
                self._reserved.add(f"{m.group(1).upper()}_{m.group(2)}")
            self._avoid.add(normalise(text))

    # -- replacement -----------------------------------------------------
    def _next_key(self, entity_type: str) -> str:
        label = label_for(entity_type)
        while True:
            self._counters[label] += 1
            key = f"{label}_{self._counters[label]}"
            if key not in self._reserved:
                return key

    def _entry(self, entity_type: str, surface: str):
        base, inflection = self._split_morph.split(entity_type, surface)
        if not base.strip():
            base, inflection = surface, NO_INFLECTION
        norm = (entity_type, normalise(base))
        entry = self._entries.get(norm)
        if entry is None:
            entry = self.strategy.new_entry(
                self._next_key(entity_type), entity_type, base, self._avoid_values(), self.morphology
            )
            self._entries[norm] = entry
            self._ordered.append(entry)
        return entry, inflection

    def register(self, entity_type: str, surface: str) -> None:
        """Pre-register a mention so that numbering follows reading order
        (Presidio's anonymizer applies operators from the end of the text)."""
        self._entry(entity_type, surface)

    def replacement_for(self, entity_type: str, surface: str) -> str:
        """Return the text that replaces ``surface`` (one detected span)."""
        entry, inflection = self._entry(entity_type, surface)
        self._counts[entity_type] += 1
        return self.strategy.render(entry, inflection, self.morphology)

    def _avoid_values(self) -> set[str]:
        """Values a new pseudonym must not collide with: everything in the
        input texts plus replacements already handed out."""
        used = {normalise(e.replacement) for e in self._ordered}
        return used | self._avoid

    # -- pure-python application (Presidio's AnonymizerEngine is used in the
    # analyzer service; this path is used by tests and as a fallback) -------
    def apply(self, text: str, spans: Sequence[Span]) -> str:
        out: list[str] = []
        pos = 0
        for span in resolve_overlaps(spans):
            out.append(text[pos : span.start])
            out.append(self.replacement_for(span.entity_type, text[span.start : span.end]))
            pos = span.end
        out.append(text[pos:])
        return "".join(out)

    # -- results -----------------------------------------------------------
    @property
    def mapping(self) -> Mapping:
        return Mapping(strategy=self.strategy.name, language=self.language, entries=list(self._ordered))

    @property
    def entity_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def result(self, masked_texts: list[str]) -> MaskResult:
        return MaskResult(masked_texts=masked_texts, mapping=self.mapping, entity_counts=self.entity_counts)


def resolve_overlaps(spans: Sequence[Span]) -> list[Span]:
    """Greedy overlap resolution.

    A span that fully contains another one wins regardless of score (privacy
    first: an NER "Budapest" inside a full postal address must not leave the
    rest of the address unmasked). Remaining conflicts: higher score, then
    longer span.
    """
    valid = [s for s in spans if s.end > s.start]
    contained = {
        id(s)
        for s in valid
        for o in valid
        if o is not s and o.start <= s.start and s.end <= o.end and (o.end - o.start) > (s.end - s.start)
    }
    ranked = sorted(valid, key=lambda s: (id(s) in contained, -s.score, -(s.end - s.start), s.start))
    kept: list[Span] = []
    for s in ranked:
        if all(s.end <= k.start or s.start >= k.end for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s.start)
