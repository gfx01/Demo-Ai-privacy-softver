"""Data model shared by masking, storage and restoration.

A ``Mapping`` is the secret that links placeholders back to original values.
It must never be logged, never be put into the LiteLLM request dict and only be
persisted encrypted (see ``privacy_hub.store``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MAPPING_FORMAT_VERSION = 1


@dataclass(frozen=True)
class Span:
    """A detected entity in a text (character offsets, end exclusive)."""

    entity_type: str
    start: int
    end: int
    score: float = 1.0

    def text(self, source: str) -> str:
        return source[self.start : self.end]


@dataclass
class MappingEntry:
    """One original value and the replacement that stands in for it.

    ``original`` and ``replacement`` are *base forms*: when a Hungarian case
    suffix was split off the detected span (``Kovács Péterrel`` ->
    ``Kovács Péter`` + instrumental), it is not part of either value.
    ``parts`` maps replacement name parts to original parts (pseudonyms only),
    e.g. ``[["Szabó", "Kovács"], ["Gábor", "Péter"]]`` so that a model answer
    mentioning only ``Gábor`` can still be restored.
    """

    key: str
    entity_type: str
    original: str
    replacement: str
    parts: list[list[str]] = field(default_factory=list)


@dataclass
class Mapping:
    strategy: str
    language: str = "en"
    entries: list[MappingEntry] = field(default_factory=list)
    version: int = MAPPING_FORMAT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "strategy": self.strategy,
            "language": self.language,
            "entries": [asdict(e) for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Mapping":
        if data.get("version") != MAPPING_FORMAT_VERSION:
            raise ValueError(f"unsupported mapping version {data.get('version')!r}")
        return cls(
            strategy=data["strategy"],
            language=data.get("language", "en"),
            entries=[MappingEntry(**e) for e in data.get("entries", [])],
        )

    def __len__(self) -> int:
        return len(self.entries)


@dataclass
class MaskResult:
    masked_texts: list[str]
    mapping: Mapping
    entity_counts: dict[str, int]

    @property
    def entity_total(self) -> int:
        return sum(self.entity_counts.values())
