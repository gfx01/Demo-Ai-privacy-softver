"""Reversible masking: placeholder strategies, morphology and restoration."""

from .model import Mapping, MappingEntry, MaskResult, Span
from .morphology import Inflection, Morphology, NullMorphology
from .restore import Restorer, StreamingRestorer
from .session import MaskingSession, resolve_overlaps
from .strategies import Strategy, get_strategy


# One morphology per (language, lexicon object): building one copies the
# lexicon (~10,000 names) and starts with an empty word cache, which cost
# ~15 ms on every masking call. The object holds no per-request state.
_MORPHOLOGIES: dict[tuple[str, int], tuple[object, Morphology]] = {}


def get_morphology(language: str, lexicon=None) -> Morphology:
    """Morphology for a language code; Hungarian gets suffix handling.
    ``lexicon`` (known names) prevents false splits when masking."""
    if not language.lower().startswith("hu"):
        return NullMorphology()
    key = (language.lower(), id(lexicon))
    hit = _MORPHOLOGIES.get(key)
    if hit is None or hit[0] is not lexicon:  # id() can be reused after the object is gone
        from .hungarian import HungarianMorphology

        hit = (lexicon, HungarianMorphology(lexicon=lexicon))
        _MORPHOLOGIES[key] = hit
    return hit[1]


def build_restorer(mapping: Mapping) -> Restorer:
    return get_strategy(mapping.strategy).restorer(mapping, get_morphology(mapping.language))


__all__ = [
    "Inflection",
    "Mapping",
    "MappingEntry",
    "MaskResult",
    "MaskingSession",
    "Morphology",
    "NullMorphology",
    "Restorer",
    "Span",
    "Strategy",
    "StreamingRestorer",
    "build_restorer",
    "get_morphology",
    "get_strategy",
    "resolve_overlaps",
]
