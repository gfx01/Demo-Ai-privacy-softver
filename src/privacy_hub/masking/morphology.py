"""Morphology hooks used by masking and restoration.

Hungarian is agglutinative: ``Kovács Péterrel`` is the name ``Kovács Péter``
plus the instrumental case suffix. If a detected span is replaced as a whole,
the grammatical information is lost and the model's answer cannot be restored
with the right ending. A ``Morphology`` therefore

* ``split``s a detected surface form into a base form and an ``Inflection``,
* ``parse_suffix``es whatever ending the model attached to a placeholder, and
* ``inflect``s the original base form with that ending during restoration.

``NullMorphology`` (used for English) never splits and re-attaches suffixes
verbatim. The Hungarian implementation lives in ``hungarian.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Inflection:
    """A case ending. ``case`` is a canonical tag (e.g. ``"INS"``) or None
    when the ending is absent or not understood; ``raw`` is the ending exactly
    as it appeared (possibly with a leading hyphen)."""

    case: str | None = None
    raw: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.raw and self.case is None


NO_INFLECTION = Inflection()


class Morphology(Protocol):
    name: str

    def split(self, entity_type: str, surface: str) -> tuple[str, Inflection]:
        """Split a detected surface form into (base form, inflection)."""

    def parse_suffix(self, raw: str) -> Inflection:
        """Interpret an ending found after a placeholder in model output."""

    def inflect(self, base: str, inflection: Inflection, entity_type: str) -> str:
        """Render ``base`` with ``inflection`` (used to restore originals and to
        render pseudonyms)."""

    def placeholder_suffix(self, inflection: Inflection) -> str:
        """Ending to write after a bracket token, e.g. ``-vel``."""


class NullMorphology:
    """No suffix handling: spans are replaced whole, endings re-attached as-is."""

    name = "null"

    def split(self, entity_type: str, surface: str) -> tuple[str, Inflection]:
        return surface, NO_INFLECTION

    def parse_suffix(self, raw: str) -> Inflection:
        return Inflection(None, raw)

    def inflect(self, base: str, inflection: Inflection, entity_type: str) -> str:
        return base + inflection.raw

    def placeholder_suffix(self, inflection: Inflection) -> str:
        return inflection.raw
