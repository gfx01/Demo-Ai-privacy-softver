"""Restoration of original values in model output (batch and streaming)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from .model import Mapping, MappingEntry
from .morphology import Morphology
from .strategies import EXISTING_TOKEN_RE

# Letters that can form a (Hungarian or English) suffix glued to a token.
LETTERS = "A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüűÄäËëÏïÔôÕõÛûÂâÊêÎîŔŕ"
MAX_SUFFIX_LEN = 12
HYPHENS = "-‐‑–"  # hyphen-minus, hyphen, non-breaking hyphen, en dash
SUFFIX_PATTERN = rf"(?P<suf>[{HYPHENS}]?[{LETTERS}]{{1,{MAX_SUFFIX_LEN}}}(?![{LETTERS}]))?"


@dataclass(frozen=True)
class RestoreMatch:
    start: int
    end: int
    entry: MappingEntry
    replacement: str


class Restorer(ABC):
    """Finds placeholders (in any tolerated variant) and yields replacements."""

    def __init__(self, mapping: Mapping, morphology: Morphology) -> None:
        self.mapping = mapping
        self.morphology = morphology
        self.restored: Counter[str] = Counter()

    #: upper bound of a single match's length (+ lookahead), used by streaming
    max_match_len: int = 0

    @abstractmethod
    def iter_matches(self, text: str, pos: int = 0) -> Iterator[RestoreMatch]:
        """Non-overlapping matches in ``text`` starting at or after ``pos``.
        Look-behind assertions may inspect characters before ``pos``."""

    def restore(self, text: str) -> str:
        out: list[str] = []
        last = 0
        for m in self.iter_matches(text):
            out.append(text[last : m.start])
            out.append(m.replacement)
            self.restored[m.entry.key] += 1
            last = m.end
        out.append(text[last:])
        return "".join(out)

    def leftover_tokens(self, text: str) -> list[str]:
        """Bracket-looking tokens still present after restoration (e.g. tokens
        the model invented). Used for metrics; never contains original values."""
        return [m.group(0) for m in EXISTING_TOKEN_RE.finditer(text)]


class BracketRestorer(Restorer):
    """Restores ``[PERSON_1]`` and tolerated variants:

    ``[PERSON_1]``, ``[person_1]``, ``[PERSON 1]``, ``[ PERSON_1 ]``,
    ``PERSON_1`` (bare, must be upper case with underscore), each optionally
    followed by a Hungarian ending (``-nek``, ``nek``, ``–nek``).
    Tokens that are not in the mapping are left untouched.
    """

    def __init__(self, mapping: Mapping, morphology: Morphology) -> None:
        super().__init__(mapping, morphology)
        self.by_key = {e.key.upper(): e for e in mapping.entries}
        labels = sorted({k.rsplit("_", 1)[0] for k in self.by_key}, key=len, reverse=True)
        if labels:
            alt = "|".join(re.escape(lab) for lab in labels)
            bracketed = rf"\[\s*(?P<bl>(?i:{alt}))[ _\-]?(?P<bn>\d+)\s*\]"
            bare = rf"(?<![\w\[])(?P<pl>{alt})_(?P<pn>\d+)(?!\d)"
            self._re: re.Pattern[str] | None = re.compile(rf"(?:{bracketed}|{bare}){SUFFIX_PATTERN}")
            longest_key = max(len(k) for k in self.by_key)
        else:
            self._re = None
            longest_key = 0
        # "[" + spaces + key + spaces + "]" + suffix (+hyphen) + 1 look-ahead char
        self.max_match_len = longest_key + 2 + 4 + MAX_SUFFIX_LEN + 1 + 1

    def iter_matches(self, text: str, pos: int = 0) -> Iterator[RestoreMatch]:
        if self._re is None:
            return
        for m in self._re.finditer(text, pos):
            label = (m.group("bl") or m.group("pl")).upper()
            number = m.group("bn") or m.group("pn")
            entry = self.by_key.get(f"{label}_{int(number)}")
            if entry is None:
                continue
            inflection = self.morphology.parse_suffix(m.group("suf") or "")
            yield RestoreMatch(
                m.start(), m.end(), entry, self.morphology.inflect(entry.original, inflection, entry.entity_type)
            )


class StreamingRestorer:
    """Incremental restoration for streamed model output.

    Text is held back only while it could still be part of a placeholder
    match: the last ``max_match_len`` characters, or less when a complete
    match ends earlier. ``flush()`` must be called at end of stream.
    """

    CONTEXT = 8  # already-emitted source chars kept for look-behind

    def __init__(self, restorer: Restorer) -> None:
        self.restorer = restorer
        self._ctx = ""
        self._buf = ""

    @property
    def pending(self) -> str:
        return self._buf

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._buf += chunk
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        text = self._ctx + self._buf
        base = len(self._ctx)
        cut = len(text) if final else len(text) - self.restorer.max_match_len
        if cut <= base:
            return ""
        out: list[str] = []
        last = base
        for m in self.restorer.iter_matches(text, base):
            if m.start >= cut:
                break
            if m.end > cut:  # only possible when not final
                cut = m.start
                break
            out.append(text[last : m.start])
            out.append(m.replacement)
            self.restorer.restored[m.entry.key] += 1
            last = m.end
        if last < cut:
            out.append(text[last:cut])
        self._ctx = text[max(0, cut - self.CONTEXT) : cut]
        self._buf = text[cut:]
        return "".join(out)
