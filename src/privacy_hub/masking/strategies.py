"""Placeholder strategies.

* ``bracket``   – ``[PERSON_1]``; Hungarian endings are written after a hyphen
                  (``[PERSON_1]-vel``), the way Hungarian attaches suffixes to
                  symbols and abbreviations.
* ``pseudonym`` – consistent, plausible fake values (``Szabó Gáborral``),
                  see ``pseudonyms.py`` (added in milestone 3).

A strategy decides what replaces an original value; the ``Restorer`` built by
``strategy.restorer(...)`` reverses it in model output.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .labels import label_for
from .model import Mapping, MappingEntry
from .morphology import Inflection, Morphology

if TYPE_CHECKING:
    from .restore import Restorer

# Anything in the *input* that already looks like one of our bracket tokens.
EXISTING_TOKEN_RE = re.compile(r"\[\s*([A-Za-z][A-Za-z0-9_]*?)[ _-]?(\d+)\s*\]")


class Strategy(ABC):
    name: str
    # Whether detected spans are split into base form + case ending.
    splits_suffix: bool = True

    @abstractmethod
    def new_entry(
        self, key: str, entity_type: str, original: str, avoid: set[str], morphology: Morphology
    ) -> MappingEntry:
        """Create the mapping entry for a newly seen original value."""

    @abstractmethod
    def render(self, entry: MappingEntry, inflection: Inflection, morphology: Morphology) -> str:
        """Text that replaces one occurrence of the original in the prompt."""

    @abstractmethod
    def restorer(self, mapping: Mapping, morphology: Morphology) -> "Restorer":
        """Build the restorer that maps model output back to originals."""


class BracketStrategy(Strategy):
    name = "bracket"

    def new_entry(self, key, entity_type, original, avoid, morphology):
        return MappingEntry(key=key, entity_type=entity_type, original=original, replacement=f"[{key}]")

    def render(self, entry, inflection, morphology):
        return entry.replacement + morphology.placeholder_suffix(inflection)

    def restorer(self, mapping, morphology):
        from .restore import BracketRestorer

        return BracketRestorer(mapping, morphology)


class NaiveBracketStrategy(BracketStrategy):
    """Baseline for evaluation only: the whole detected span (including any
    Hungarian case ending) is swallowed by the token, so the model never sees
    the grammatical case and restoration always yields the original span."""

    name = "bracket_naive"
    splits_suffix = False

    def render(self, entry, inflection, morphology):
        return entry.replacement

    def restorer(self, mapping, morphology):
        from .morphology import NullMorphology
        from .restore import BracketRestorer

        return BracketRestorer(mapping, NullMorphology())


def key_for(entity_type: str, number: int) -> str:
    return f"{label_for(entity_type)}_{number}"


def get_strategy(name: str) -> Strategy:
    if name == "bracket":
        return BracketStrategy()
    if name == "bracket_naive":
        return NaiveBracketStrategy()
    if name == "pseudonym":
        from .pseudonyms import PseudonymStrategy

        return PseudonymStrategy()
    raise ValueError(f"unknown masking strategy {name!r}")
