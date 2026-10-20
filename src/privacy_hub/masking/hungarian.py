"""Rule-based Hungarian case morphology for names and company names.

Scope: the case endings that commonly attach to person / company / place
names. Implemented rules (standard Hungarian orthography):

* vowel harmony (back / front-unrounded / front-rounded) decided by the last
  vowel; a single neutral i, í, é after a back vowel keeps the word back
  ("Dávidnak", "Zsófinak", "Kornélnak"), two neutral vowels lean front
  ("Noéminek"; such words vacillate in real usage), "Ágnesnek", "Budapesten",
* stem-final a/e lengthening ("Anna" -> "Annának", "Csabával"),
* assimilation of the v in -val/-vel and -vá/-vé to the final consonant,
  including digraph doubling ("Kovács" -> "Kováccsal", "Nagy" -> "Naggyal")
  and double consonants ("Kiss" -> "Kissel"),
* old-orthography surnames keep their spelling and the suffix takes the
  pronounced consonant ("Tóth" -> "Tóthtal", "Rácz" -> "Ráczcal",
  "Széchenyi" -> "Széchenyivel"),
* names ending in a double consonant keep it and a suffix starting with the
  same consonant is hyphenated ("Kiss-sel", "Papp-pal", "Mariann-nak"),
* accusative with/without linking vowel ("Pétert", "Kovácsot", "Tóthot"),
* abbreviations and legal forms take a hyphen ("Kft.-vel", "Bt.-t").

Known gaps (documented in docs/LIMITATIONS.md): irregular stems, foreign
names whose pronunciation differs from spelling, plural/possessive forms,
numbers ("123-as") whose suffix depends on how the number is read aloud.

``split`` is analysis-by-synthesis: a candidate (stem, case) is accepted only
if ``inflect(stem, case)`` reproduces the surface form exactly. An optional
lexicon of known names prevents false splits ("Zsolt" is not "Zsol" + -t).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .morphology import NO_INFLECTION, Inflection

BACK = set("aáoóuú")
FRONT_ROUNDED = set("öőüű")
FRONT_UNROUNDED = set("eéií")
VOWELS = BACK | FRONT_ROUNDED | FRONT_UNROUNDED

# case -> (back, front_unrounded, front_rounded) suffix after a vowel-final stem
SUFFIXES: dict[str, tuple[str, str, str]] = {
    "DAT": ("nak", "nek", "nek"),
    "INE": ("ban", "ben", "ben"),
    "ILL": ("ba", "be", "be"),
    "ELA": ("ból", "ből", "ből"),
    "SUB": ("ra", "re", "re"),
    "DEL": ("ról", "ről", "ről"),
    "ADE": ("nál", "nél", "nél"),
    "ABL": ("tól", "től", "től"),
    "ALL": ("hoz", "hez", "höz"),
    "TER": ("ig", "ig", "ig"),
    "CAU": ("ért", "ért", "ért"),
    "ESS": ("ként", "ként", "ként"),
    "POSS": ("é", "é", "é"),
    "INS": ("val", "vel", "vel"),
    "TRA": ("vá", "vé", "vé"),
    "SUP": ("n", "n", "n"),
    "ACC": ("t", "t", "t"),
}
# Suffix forms after a consonant (linking vowel) where they differ.
SUP_AFTER_CONSONANT = ("on", "en", "ön")
ACC_LINKING = ("ot", "et", "öt")
CASES = tuple(SUFFIXES)

# No a/e -> á/é lengthening before these.
NO_LENGTHENING = {"ESS"}
DIGRAPHS = ("dzs", "cs", "dz", "gy", "ly", "ny", "sz", "ty", "zs")
# Consonants after which the accusative -t attaches directly.
DIRECT_T = ("j", "l", "ly", "n", "ny", "r", "s", "sz", "z", "zs")
# Old-orthography endings -> pronounced consonant (for assimilation/linking).
OLD_ORTHO_FINAL = {
    "cz": "c",
    "ch": "cs",
    "th": "t",
    "ts": "cs",
    "tz": "c",
    "gh": "g",
    "ph": "f",
    "w": "v",
    "x": "sz",  # "Max" -> "Maxszal"
}
# Legal forms / abbreviations: written with a hyphen; the ending follows the
# pronounced form (AkH): "Kft." = "káefté" -> front ("Kft.-vel"), but
# "Kkt." = "kákáté", "Kht." = "káháté", "Kv." = "kávé" -> back ("Kkt.-val",
# like AkH's "BKV-nál"). Usage of "Kkt.-vel" is also attested (analogy with
# Kft.); see eval/data/inflection_gold*.json.
ABBREVIATION_RE = re.compile(r"(?:Kft|Zrt|Nyrt|Bt|Kkt|Rt|Kht|Kv|Ltd|e\.\s?v)\.$")
BACK_ABBREVIATION_RE = re.compile(r"(?:Kkt|Kht|Kv)\.$", re.IGNORECASE)

# Parsing: every written suffix variant -> case (harmony is ignored).
_PARSE: dict[str, str] = {}
for _case, _forms in SUFFIXES.items():
    for _f in _forms:
        _PARSE.setdefault(_f, _case)
for _f in SUP_AFTER_CONSONANT:
    _PARSE[_f] = "SUP"
for _f in ACC_LINKING + ("at",):
    _PARSE[_f] = "ACC"


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


NEUTRAL = set("iíé")


def harmony(word: str) -> int:
    """0 = back, 1 = front unrounded, 2 = front rounded.

    The last vowel decides; a neutral i/í/é right after a back vowel keeps
    the word back ("Dávid", "Kornél"); two trailing neutral vowels after a
    back vowel lean front ("Noémi"); "Lili", "Ágnes", "Budapest" are front."""
    vowels = [c for c in word.lower() if c in VOWELS]
    if not vowels:
        return 1  # e.g. "Kft" is pronounced with front vowels
    last = vowels[-1]
    if last in BACK:
        return 0
    if last in FRONT_ROUNDED:
        return 2
    if last in NEUTRAL and len(vowels) >= 2 and vowels[-2] in BACK:
        return 0
    return 1


def _final_consonant(word: str) -> tuple[str, str]:
    """(written ending, pronounced consonant) of a consonant-final word."""
    w = word.lower()
    for written, spoken in OLD_ORTHO_FINAL.items():
        if w.endswith(written):
            return written, spoken
    for dg in DIGRAPHS:
        if w.endswith(dg):
            # a doubled digraph like "ssz" counts as the digraph
            return dg, dg
    return w[-1], w[-1]


def _ends_in_vowel(word: str) -> bool:
    w = word.lower()
    if not w:
        return False
    if w[-1] in VOWELS:
        return True
    # old-orthography final y after a consonant is read as i ("Széchenyi" has i,
    # "Dessewffy", "Thököly" end in y): ly / ny / gy / ty are consonant digraphs
    return w.endswith("y") and not re.search(r"(?:l|n|g|t)y$", w)


def _lengthen(word: str, case: str) -> str:
    if case in NO_LENGTHENING:
        return word
    if word.endswith("a"):
        return word[:-1] + "á"
    if word.endswith("e"):
        return word[:-1] + "é"
    return word


def _double_written(written: str) -> str:
    """Written form of a doubled consonant: cs -> ccs, sz -> ssz, dzs -> ddzs."""
    if len(written) == 1:
        return written + written
    if written == "dzs":
        return "ddzs"
    return written[0] + written


def inflect_word(word: str, case: str | None) -> str:
    """Inflect a single word (the last word of a name)."""
    if not case or case == "NOM" or case not in SUFFIXES:
        return word
    if ABBREVIATION_RE.search(word) or word.endswith(".") or word[-1:].isdigit():
        return word + "-" + _abbreviation_suffix(case, word)
    h = harmony(word)
    suffix = SUFFIXES[case][h]
    if _ends_in_vowel(word):
        return _lengthen(word, case) + suffix
    written, spoken = _final_consonant(word)
    lower = word.lower()
    if case in ("INS", "TRA"):
        tail = suffix[1:]  # "al"/"el"/"á"/"é"
        if written in OLD_ORTHO_FINAL:
            # name keeps its spelling, suffix starts with the spoken consonant
            return word + spoken + tail
        if _double_final(lower):
            return word + "-" + lower[-1] + tail  # Kiss -> Kiss-sel, Papp -> Papp-pal
        if len(written) > 1 and lower.endswith(written[0] + written):
            return word + tail  # already doubled digraph ("Hosszú"? rare)
        return word[: len(word) - len(written)] + _double_written(written) + tail
    if case == "SUP":
        return word + SUP_AFTER_CONSONANT[h]
    if case == "ACC":
        # j l ly n ny r s sz z zs (and double s: "Kisst") take a bare -t,
        # except a sibilant after r/l: "Örs" -> "Örsöt", "Bors" -> "Borsot"
        before = lower[: len(lower) - len(written)]
        if written in ("s", "sz", "z", "zs") and before[-1:] in ("r", "l"):
            return word + ACC_LINKING[h]
        if written in DIRECT_T or lower.endswith("ss"):
            return word + "t"
        return word + ACC_LINKING[h]
    if _double_final(lower) and suffix[0] == lower[-1]:
        return word + "-" + suffix  # Mariann-nak, Nikolett-től: no triple letters in names
    return word + suffix


def _double_final(lower: str) -> bool:
    return len(lower) >= 2 and lower[-1] == lower[-2] and lower[-1] not in VOWELS


def _abbreviation_suffix(case: str, word: str = "") -> str:
    # pronounced vowel-final: "Kft.-nek", "Kft.-vel", "Kft.-t"; "Kkt.-nak"
    return SUFFIXES[case][0 if BACK_ABBREVIATION_RE.search(word) else 1]


def inflect(base: str, case: str | None) -> str:
    """Inflect a (possibly multi-word) name: the last word carries the case."""
    if not case or case == "NOM":
        return base
    head, sep, last = base.rpartition(" ")
    if not last:
        return base
    return head + sep + inflect_word(last, case)


def parse_raw_suffix(raw: str, stem: str = "") -> str | None:
    """Map an ending as written by a model to a case.

    ``stem`` is the text the ending is glued to (pseudonym form); it lets us
    recognise assimilated forms ("Gábor" + "ral" -> INS) and lengthened stems.
    """
    s = raw.lstrip("-‐‑–").lower()
    if not s:
        return None
    if s in _PARSE:
        return _PARSE[s]
    if stem:
        st = stem.lower()
        # assimilated -val/-vel and -vá/-vé: first consonant repeats the stem's
        _, spoken = _final_consonant(st) if not _ends_in_vowel(st) else ("", "")
        if spoken and s.startswith(spoken) and s[len(spoken) :] in ("al", "el", "á", "é"):
            return "INS" if s.endswith("l") else "TRA"
        if st.endswith(("cs", "sz", "zs", "gy", "ny", "ly", "ty")) and s[1:] in ("al", "el", "á", "é"):
            return "INS" if s.endswith("l") else "TRA"
    return None


class HungarianMorphology:
    name = "hu"

    def __init__(self, lexicon: Iterable[str] | None = None) -> None:
        self.lexicon = {_nfc(w).lower() for w in (lexicon or ())}
        self._cache: dict[str, tuple[str, str | None, str]] = {}

    # -- analysis ----------------------------------------------------------
    def split_word(self, word: str) -> tuple[str, str | None, str]:
        """(stem, case, raw_suffix) for one word; case None if not inflected."""
        hit = self._cache.get(word)
        if hit is None:
            if len(self._cache) > 50000:
                self._cache.clear()
            hit = self._cache[word] = self._split_word(word)
        return hit

    def _split_word(self, word: str) -> tuple[str, str | None, str]:
        w = _nfc(word)
        if len(w) < 3 or w.lower() in self.lexicon:
            return w, None, ""
        m = re.match(r"^(.*\.)[-‐‑–]([a-záéíóöőúüű]+)$", w)
        if m and ABBREVIATION_RE.search(m.group(1)):
            case = _PARSE.get(m.group(2))
            if case:
                return m.group(1), case, "-" + m.group(2)
        # (known_in_lexicon, productive_rule, stem, case, raw_suffix)
        candidates: list[tuple[int, int, str, str, str]] = []
        for i in range(2, len(w)):
            prefix, rest = w[:i], w[i:]
            stems = {prefix}
            unlengthened = None
            if prefix.endswith("á"):
                unlengthened = prefix[:-1] + "a"
            elif prefix.endswith("é"):
                unlengthened = prefix[:-1] + "e"
            if unlengthened:
                stems.add(unlengthened)
            # digraph doubling: "Kovác" + "csal" -> stem "Kovács"
            for dg in DIGRAPHS:
                if rest.startswith(dg) and prefix.endswith(dg[0]):
                    stems.add(prefix[:-1] + dg)
            if len(rest) >= 2 and rest[0] == prefix[-1:]:
                stems.add(prefix)
            for stem in stems:
                if len(stem) < 2 or not stem[0].isalpha():
                    continue
                for case in CASES:
                    if inflect_word(stem, case) == w:
                        known = 1 if stem.lower() in self.lexicon else 0
                        productive = 1 if stem == unlengthened else 0  # "Annának" -> "Anna", not "Anná"
                        candidates.append((known, productive, stem, case, rest))
        if not candidates and self.lexicon:
            candidates = self._lenient_candidates(w)
        if not candidates:
            return w, None, ""
        pool = [c for c in candidates if c[0] == 1]
        if not pool:
            # Unknown stem: accept only endings of 2+ letters; a bare -t / -n /
            # -é would split too many nominatives ("Zsolt", "Kovácsné").
            pool = [c for c in candidates if len(c[4]) >= 2 and c[3] not in ("ACC", "SUP", "POSS")]
            if not pool:
                return w, None, ""
        # Prefer known stems, then the productive (lengthening) analysis, then
        # common cases, then stems not ending in a doubled consonant ("Zoltánnal" -> "Zoltán",
        # not "Zoltánn"), then the longest stem ("Péternek" -> "Péter").
        # The translative ("Marcellé" = Marcel + -vé) is rare with names: the
        # possessive reading (Marcell + -é) wins.
        def rank(c):
            stem = c[2].lower()
            single_final = 0 if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in VOWELS else 1
            return (c[0], c[1], c[3] != "TRA", single_final, len(stem))

        _, _, stem, case, raw = max(pool, key=rank)
        return stem, case, raw

    def _lenient_candidates(self, w: str) -> list[tuple[int, int, str, str, str]]:
        """Known name + any written variant of an ending, ignoring harmony
        (input text may use a vacillating form the rules do not produce)."""
        out = []
        for form, case in _PARSE.items():
            if len(form) < 2 or not w.lower().endswith(form):
                continue
            stem = w[: len(w) - len(form)].rstrip("-\u2010\u2011\u2013")
            for candidate in {stem, stem[:-1] + "a" if stem.endswith("á") else stem, stem[:-1] + "e" if stem.endswith("é") else stem}:
                if candidate.lower() in self.lexicon:
                    out.append((1, 0, candidate, case, w[len(stem):]))
        return out

    def split(self, entity_type: str, surface: str) -> tuple[str, Inflection]:
        text = surface.strip()
        if not text:
            return surface, NO_INFLECTION
        m = re.match(r"^(.*?)[-‐‑–]([a-záéíóöőúüű]{1,6})$", text)
        if m and (m.group(1).endswith(".") or m.group(1)[-1:].isdigit()):
            case = _PARSE.get(m.group(2))
            return m.group(1), Inflection(case, "-" + m.group(2))
        if entity_type not in INFLECTABLE_TYPES:
            return surface, NO_INFLECTION
        head, sep, last = text.rpartition(" ")
        stem, case, raw = self.split_word(last)
        if case is None:
            return surface, NO_INFLECTION
        return head + sep + stem, Inflection(case, raw)

    # -- generation ----------------------------------------------------------
    def parse_suffix(self, raw: str, stem: str = "") -> Inflection:
        return Inflection(parse_raw_suffix(raw, stem), raw)

    def inflect(self, base: str, inflection: Inflection, entity_type: str) -> str:
        if entity_type not in INFLECTABLE_TYPES:
            # Amounts, identifiers, e-mails, phone numbers: restore the exact
            # value and keep the model's ending, hyphenated as Hungarian does
            # after numbers and codes ("1 250 000 Ft-ot", "...@example.com-ra").
            # The ending is not re-harmonised: that would need the spoken form.
            raw = inflection.raw
            if not raw:
                return base
            return base + (raw if raw.startswith(("-", "‐", "‑", "–")) else "-" + raw)
        if inflection.case:
            return inflect(base, inflection.case)
        if not inflection.raw:
            return base
        raw = inflection.raw
        # unknown ending: glue to words, hyphenate after abbreviations/numbers
        if base.endswith(".") or base[-1:].isdigit() or base[-1:] in ")]":
            return base + ("" if raw.startswith(("-", "–")) else "-") + raw
        return base + raw.lstrip("-‐‑–")

    def placeholder_suffix(self, inflection: Inflection) -> str:
        if not inflection.case:
            return ""
        return "-" + _abbreviation_suffix(inflection.case) if inflection.case in SUFFIXES else ""


INFLECTABLE_TYPES = {"PERSON", "HU_COMPANY", "ORGANIZATION", "LOCATION", "HU_PERSON"}
