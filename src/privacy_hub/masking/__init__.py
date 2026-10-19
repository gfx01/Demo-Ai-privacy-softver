"""Reversible masking: placeholder strategies, morphology and restoration."""

from .model import Mapping, MappingEntry, MaskResult, Span
from .morphology import Inflection, Morphology, NullMorphology
from .restore import Restorer, StreamingRestorer
from .session import MaskingSession, resolve_overlaps
from .strategies import Strategy, get_strategy


def get_morphology(language: str) -> Morphology:
    """Morphology for a language code; Hungarian gets suffix handling."""
    if language.lower().startswith("hu"):
        try:
            from .hungarian import HungarianMorphology
        except ImportError:  # milestone 1: Hungarian morphology not built yet
            return NullMorphology()
        return HungarianMorphology()
    return NullMorphology()


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
