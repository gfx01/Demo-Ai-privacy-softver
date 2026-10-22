"""Opt-in "maximum" detection level: mask what is not known to be harmless.

The standard recognizers look for things that are known to be personal data
(names from a lexicon, identifiers, addresses ...). Whatever they do not know
leaks. This pass turns the logic around and masks, on top of the standard
results, every token that is not known to be an ordinary word:

* capitalised words inside a sentence (Hungarian capitalises proper nouns
  only), except the polite pronoun "Ön", contract roles ("Megrendelő") and
  titles; at the start of a sentence a capitalised word is masked when it
  is not an ordinary word or is a known name or place;
* the whole run of capitalised words around a masked one ("Kalibra
  Mérnökiroda"), with the standard type when the run touches a name the
  standard recognizers found ("Kovács" next to PERSON "Péter");
* every later mention of a word masked once ("Kovács szerint ...");
* acronyms (except a short list of generic ones: EU, ÁFA, PDF ...);
* lower-case words that are not ordinary words (typos, user names, names
  typed in lower case) or that are names / places;
* digit sequences with 6+ digits, letter-digit codes, full dates of past
  years that may be birth dates, URLs.

Ordinary words: the 46,702 most frequent Hungarian word forms (wordfreq) and
the Hunspell lemmas, plus a stem + ending analysis whose endings are learnt
from those two lists. Everything else is assumed to be a name.

The price is usability: product names, brand names, rare words and dates
of past years are masked too, and the model sees more placeholders. Measured
numbers are in docs/EVALUATION.md. It cannot reach 100% either: a person's
name that is also an ordinary word at the start of a sentence ("Fekete
szerint ...") is not masked unless it was masked elsewhere in the text.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from presidio_analyzer import RecognizerResult

from privacy_hub.masking.hungarian import HungarianMorphology

from .recognizers.hu_names import LETTER, MAX_TOKEN, NICKNAMES, covered_map

SCORE = 0.35
# Longer words are not analysed (the analysis is quadratic in the length):
# an unknown word that long is masked anyway.
MAX_ANALYSED = 30
FREQUENT = 3000  # rank in the word-form list below which a word is "frequent"
NAME_TYPES = {"PERSON", "LOCATION", "ORGANIZATION", "HU_COMPANY"}

WORD_RE = re.compile(rf"[{LETTER}]+(?:['’][{LETTER}]+)?(?:-[{LETTER}]+)*")
CODE_RE = re.compile(r"(?<![\w@])[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*(?![\w@])")
DIGITS_RE = re.compile(r"(?<![\w])\d(?:[\d]|[ .\-/](?=\d)){5,40}(?!\w)")
URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']{2,300}")
_MONTHS = r"(?:jan|feb|márc|marc|ápr|apr|máj|maj|jún|jun|júl|jul|aug|szep|okt|nov|dec)[a-záéíóöőúüű]{0,9}\.?"
DATE_RE = re.compile(
    rf"(?<!\d)(?:(?P<y1>19\d\d|20\d\d)\s?[.\-/]\s?(?:\d{{1,2}}\s?[.\-/]\s?\d{{1,2}}\.?|{_MONTHS}\s?\d{{1,2}}\.?)"
    rf"|\d{{1,2}}\s?[./]\s?\d{{1,2}}\s?[./]\s?(?P<y2>19\d\d|20\d\d))(?!\d)",
    re.IGNORECASE,
)

ROMAN_RE = re.compile(r"(?=[IVXLC])C{0,3}(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")
SUFFIX_AFTER_NUMBER = re.compile(r"(?<=\d)-[a-záéíóöőúüű]{1,8}$")

# Generic acronyms that are not names of anyone.
ACRONYMS = {
    "eu", "áfa", "afa", "szja", "taj", "iban", "swift", "bic", "pdf", "doc", "docx", "xls", "xlsx", "csv", "ppt",
    "it", "hr", "ai", "mi", "ceo", "cfo", "cto", "coo", "gdpr", "ok", "usd", "eur", "huf", "gbp", "chf", "cv", "pm",
    "sms", "pin", "id", "kft", "zrt", "nyrt", "bt", "kkt", "ev", "rt", "ászf", "aszf", "sla", "kpi", "api", "url",
    "ip", "vpn", "wifi", "usb", "led", "gps", "suv", "lte", "sim", "atm", "faq", "asap", "fyi", "todo", "am", "utc",
    "cet", "cest", "ps", "nb", "kb", "mb", "gb", "tb", "kw", "kwh", "le", "km", "hu", "en", "de", "uk", "us", "usa",
    "b2b", "b2c", "crm", "erp", "qa", "ui", "ux", "seo", "ppc", "roi", "kkv", "bkv", "ügyf", "dr", "prof", "phd",
    "tsh", "ekg", "rr", "mr", "ct", "mri", "nsaid", "copd", "covid", "hiv", "mrn", "cmr", "adr", "uht", "sku", "eps",
    "kgfb", "casco", "ptk", "btk", "mt", "kresz", "gyed", "gyes", "csed", "kata", "kiva",
    "ekáer", "ekaer", "ecdl", "hm", "aha", "lol", "btw", "imo",
    "mba", "bsc", "msc", "ma", "ba", "iso", "ce", "tv", "pc", "hdmi", "dvd", "cd", "nfc", "qr", "otp", "sos", "abc",
}
# Capitalised inside a sentence without being a name: the polite pronoun,
# contract parties, titles, legal forms, salutations.
CAPITAL_OK_PREFIXES = (
    "ön", "felek", "fél", "megrendelő", "vállalkozó", "megbízó", "megbízott", "bérbeadó", "bérlő", "eladó", "vevő",
    "munkáltató", "munkavállaló", "szerződés", "szolgáltató", "előfizető", "ügyfél", "kölcsönadó", "kölcsönvevő",
    "adós", "hitelező", "társaság", "megállapodás", "melléklet", "tulajdonos", "kezes", "biztosító", "biztosított",
    "szállító", "vásárló", "felhasználó", "adatkezelő", "adatfeldolgozó", "érintett", "ajánlatkérő", "ajánlattevő",
    "kedves", "tisztelt", "üdvözlettel", "köszönettel", "úr", "uram", "hölgy", "asszony", "kisasszony", "doktor",
    "tanár", "ügyvéd", "kolléga", "igazgató", "elnök", "titkár", "főorvos", "főnök", "ügyintéző", "partner",
)
CAPITAL_OK = {"dr", "prof", "ifj", "id", "özv", "kft", "zrt", "nyrt", "bt", "kkt", "tel", "mob", "fax", "ltd", "inc",
              "gmbh", "mr", "mrs", "ms", "u", "utca", "út", "tér", "krt", "körút"}
VERB_PREFIXES = ("abba", "agyon", "alá", "által", "át", "be", "bele", "benn", "el", "ellen", "elő", "fel", "föl", "hátra",
                 "haza", "hozzá", "ide", "keresztül", "ki", "körül", "közbe", "közre", "le", "meg", "mellé", "neki",
                 "oda", "össze", "rá", "szét", "tova", "túl", "újra", "utána", "vissza", "végig")
SENTENCE_END = set(".!?:;…\n|")  # "|" separates fields in pasted tables and CVs
SKIP_BACK = set(" \t\r\"„“”'‘’()[]«»*•·–—-")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _plain(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _load(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [_nfc(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class MaximumDetector:
    def __init__(self, lexicon_dir: str | None = None) -> None:
        d = Path(lexicon_dir or os.environ.get("ANALYZER_LEXICON_DIR", "/app/data/lexicon"))
        forms = [w.lower() for w in _load(d / "word_forms.txt")]
        if not forms:
            raise RuntimeError(f"maximum detection needs {d / 'word_forms.txt'} (fetch_lexicon.py)")
        lemmas = {w.lower() for w in _load(d / "common_words.txt")}
        self.lemmas = lemmas
        self.rank = {w: i for i, w in reversed(list(enumerate(forms)))}
        self.vocab = set(forms) | lemmas
        self.plain_vocab = {_plain(w) for w in self.vocab}
        # Stems an ending can follow: lemmas, frequent forms of 4+ letters,
        # and the stem of -ik verbs ("tartozik" -> "tartoz" + "na").
        self.stems = lemmas | {w for w in forms if len(w) >= 4} | {w[:-2] for w in lemmas if w.endswith("ik") and len(w) >= 5}
        self.plain_stems = {_plain(w) for w in self.stems}
        given = {w.lower() for f in ("given_male.txt", "given_female.txt", "foreign_given.txt") for w in _load(d / f)}
        self.given_hu = {w.lower() for f in ("given_male.txt", "given_female.txt") for w in _load(d / f)}
        self.given_hu |= {n.lower() for n in NICKNAMES}
        given |= self.given_hu
        # "gabor", "jozsef": given names typed without accents
        self.plain_given_hu = {_plain(w) for w in self.given_hu if not w.isascii()}
        surnames = {w.lower() for w in _load(d / "surnames.txt")}
        places = {w.lower() for f in ("settlements.txt", "foreign_cities.txt", "countries.txt") for w in _load(d / f)
                  if " " not in w}
        # Names that are also ordinary words ("Fekete", "Rózsa", "Szeged"
        # is not one) count as names only inside a run or after a mask.
        self.names = given | surnames | places
        self.strong_names = {w for w in self.names if w not in lemmas}
        self.plain_strong = {_plain(w) for w in self.strong_names if not w.isascii()}
        self.morph = HungarianMorphology(self.names)
        # Endings: what follows a Hunspell lemma in the frequent word forms
        # ("ház|ban", "alm|ával" -> "ával" via "almá"), seen 5+ times.
        counts: Counter[str] = Counter()
        for w in forms:
            for i in range(3, len(w)):
                stem, rest = w[:i], w[i:]
                unlengthened = stem[:-1] + {"á": "a", "é": "e"}.get(stem[-1], stem[-1])
                if len(rest) <= 9 and (stem in lemmas or unlengthened in lemmas):
                    counts[rest] += 1
        self.endings = {e for e, c in counts.items() if c >= 5}
        self.plain_endings = {_plain(e) for e in self.endings}
        self._known_cache: tuple[dict[str, bool], dict[str, bool]] = ({}, {})
        self.max_birth_year = datetime.now(UTC).year - 14

    # -- word classes --------------------------------------------------------
    def known(self, word: str, depth: int = 0) -> bool:
        """An ordinary word: a frequent form, a lemma, a lemma + ending, or a
        compound of two of these. Accent-less spelling counts too."""
        low = word.lower()
        if len(low) > MAX_ANALYSED:
            return False  # no ordinary word is this long; unknown -> masked
        cache = self._known_cache[depth]
        hit = cache.get(low)
        if hit is not None:
            return hit
        if len(cache) > 100_000:
            cache.clear()
        if "-" in low:
            hit = all(self.known(p, depth) for p in low.split("-") if p)
        else:
            hit = self._known(low, depth)
        cache[low] = hit
        return hit

    def _known(self, low: str, depth: int) -> bool:
        if low in self.vocab:
            return True
        plain = _plain(low)
        if plain in self.plain_vocab:
            return True
        for prefix in VERB_PREFIXES:
            if low.startswith(prefix) and len(low) - len(prefix) >= 3 and self.known(low[len(prefix):], depth):
                return True
        for i in range(len(low) - 1, 2, -1):
            head, rest = low[:i], low[i:]
            for stem in self._stems(head, rest):
                if stem in self.stems and self._endings(rest):
                    return True
            # compound of two known words: "számlázó|programot", "bér|számfejtéshez"
            if depth == 0 and len(rest) >= 3 and self.known(head, 1) and self.known(rest, 1):
                return True
            if plain[:i] in self.plain_stems and self._endings(plain[i:], plain=True):
                return True
        return False

    @staticmethod
    def _stems(head: str, rest: str) -> list[str]:
        stems = [head]
        if head[-1] in "áé":
            stems.append(head[:-1] + {"á": "a", "é": "e"}[head[-1]])
        for dg in ("sz", "zs", "cs", "gy", "ny", "ty", "ly"):
            if rest.startswith(dg) and head.endswith(dg[0]):
                stems.append(head[:-1] + dg)
        return stems

    def _endings(self, rest: str, plain: bool = False) -> bool:
        """rest is a chain of up to three learnt endings ("aim" + "ra")."""
        pool = self.plain_endings if plain else self.endings
        if rest in pool:
            return True
        return any(rest[:i] in pool and (rest[i:] in pool or any(rest[i:j] in pool and rest[j:] in pool
                                                                    for j in range(i + 1, len(rest))))
                   for i in range(1, len(rest)))

    def is_name(self, word: str, strong: bool) -> bool:
        """A known given name, surname or place (inflected or not)."""
        pool = self.strong_names if strong else self.names
        low = word.lower()
        if low in pool:
            return True
        if len(low) > MAX_ANALYSED:
            return False
        stem = self.morph.split_word(word[:1].upper() + word[1:])[0].lower()
        # accent-less spelling of a name with accents: "bodnartol", "Gyalra"
        return stem in pool or (strong and word.isascii() and (low in self.plain_strong or stem in self.plain_strong))

    def _lower_name(self, word: str) -> bool:
        """A name typed in lower case ("zsuzsi", "bodnartol"). Frequent word
        forms ("után", "jele") are names only when they are Hungarian given
        names or nicknames ("péter")."""
        low = word.lower()
        if low in self.vocab:
            return (low in self.given_hu or low in self.plain_given_hu) and low not in self.lemmas
        return self.is_name(word, strong=True)

    def frequent(self, word: str) -> bool:
        return self.rank.get(word.lower(), FREQUENT) < FREQUENT

    # -- detection -------------------------------------------------------------
    def _sentence_start(self, text: str, start: int) -> bool:
        i = start - 1
        newline = False
        while i >= 0 and start - i <= 12 and text[i] in SKIP_BACK | {"\n"}:
            newline |= text[i] == "\n"
            i -= 1
        if i < 0 or newline:
            return True
        if text[i] not in SENTENCE_END:
            return False
        if text[i] == ".":
            # "dr. Kovács", "K. Péter", "u. 5." - abbreviations do not end a sentence
            j = i
            while j > 0 and i - j < 6 and text[j - 1].isalpha():
                j -= 1
            word = text[j:i]
            if word and (len(word) == 1 and word.isupper() or word.islower() and len(word) <= 4 and word in CAPITAL_OK | {"ifj", "özv", "id", "kb", "pl", "ill", "vö"}):
                return False
        return True

    def detect(self, text: str, results: list[RecognizerResult]) -> list[RecognizerResult]:
        covered = covered_map(len(text), [(r.start, r.end) for r in results])
        type_at: dict[int, str] = {}
        for r in results:
            if r.entity_type in NAME_TYPES:
                type_at[r.start] = r.entity_type
                type_at[r.end] = r.entity_type
        out: list[tuple[str, int, int]] = []

        def free(s: int, e: int) -> bool:
            return not any(covered[s:e])

        # Words
        tokens = [(m.start(), m.end(), m.group()) for m in WORD_RE.finditer(text) if len(m.group()) <= MAX_TOKEN]
        n = len(tokens)
        verdict: list[str | None] = [None] * n
        caps = [w[0].isupper() for _, _, w in tokens]
        exempt = [False] * n  # never masked by the run and later-mention rules
        initial = [caps[i] and self._sentence_start(text, tokens[i][0]) for i in range(n)]
        for i, (s, e, w) in enumerate(tokens):
            if not free(s, e) or len(w) < 2:
                continue
            low = w.lower()
            head = w.split("-")[0]
            core = head.lower()
            if text[e : e + 1] == "." and len(w) <= 4 and w[1:].islower():
                exempt[i] = True  # abbreviation: "özv.", "Ptk.", "röv."
            elif ROMAN_RE.fullmatch(head):
                exempt[i] = True  # "XIV. kerület", "II. félév"
            elif head.isupper() and len(head) >= 2:
                rest_known = all(self.known(p) for p in low.split("-")[1:] if p)
                if (core in ACRONYMS or _plain(core) in ACRONYMS) and rest_known:
                    exempt[i] = True
                elif len(core) <= 5 or not self.known(head) or self.is_name(head, strong=False):
                    verdict[i] = "PROPER_NOUN"
            elif caps[i]:
                if core in CAPITAL_OK or any(core.startswith(p) and self.known(core) for p in CAPITAL_OK_PREFIXES):
                    exempt[i] = True
                elif not initial[i] or not self.known(w) or self.is_name(w, strong=True):
                    verdict[i] = "PROPER_NOUN"
                elif self.frequent(w) and not self.is_name(w, strong=False):
                    exempt[i] = True  # "A", "Holnap", "Kérjük" at the start of a sentence
            elif len(w) >= 3 and text[s - 1 : s] != "-" and (not self.known(w) or self._lower_name(w)):
                verdict[i] = "RARE_WORD"

        # Capitalised runs: a masked word takes its capitalised neighbours
        # with it, and a capitalised word next to a standard name span joins
        # that span's type ("Halasi" + PERSON "Tamás").
        def joined(a: int, b: int) -> bool:
            gap = text[tokens[a][1] : tokens[b][0]]
            return gap in (" ", "-") or (gap == ". " and len(tokens[a][2]) == 1)

        changed = True
        while changed:
            changed = False
            for i in range(n):
                s, e, w = tokens[i]
                if verdict[i] or exempt[i] or not caps[i] or not free(s, e):
                    continue
                if len(w) == 1 and text[e : e + 1] != ".":
                    continue  # the article "A", not an initial
                left = i > 0 and joined(i - 1, i) and verdict[i - 1] == "PROPER_NOUN" and caps[i - 1]
                right = i + 1 < n and joined(i, i + 1) and verdict[i + 1] == "PROPER_NOUN" and caps[i + 1]
                if left or right:
                    verdict[i] = "PROPER_NOUN"
                    changed = True
                    continue
                if len(w) == 1:
                    # initial before a name: "K. Péter"
                    if text[e : e + 2] == ". " and type_at.get(e + 2) == "PERSON":
                        verdict[i] = "PERSON"
                        changed = True
                    continue
                left_type = type_at.get(_skip_space(text, s, -1))
                right_type = type_at.get(_skip_space(text, e, 1))
                # Unknown or name-like words join; so do -i adjectives before a
                # given name, the commonest surname shape ("Halasi Tamás").
                if (left_type or right_type) and (not self.known(w) or self.is_name(w, strong=False)
                                                  or (right_type == "PERSON" and low_i(w))):
                    verdict[i] = left_type or right_type
                    changed = True

        # Later mentions of a masked capitalised word anywhere in the text.
        masked_words = {tokens[i][2].lower() for i in range(n) if verdict[i] and caps[i]}
        masked_stems = {self.morph.split_word(tokens[i][2])[0].lower() for i in range(n)
                        if verdict[i] and caps[i] and len(tokens[i][2]) <= MAX_ANALYSED}
        for i in range(n):
            s, e, w = tokens[i]
            if verdict[i] or exempt[i] or not caps[i] or len(w) < 2 or not free(s, e):
                continue
            if w.lower() in masked_words or self.morph.split_word(w)[0].lower() in masked_stems:
                verdict[i] = "PROPER_NOUN"
        # One span per run of same-type words ("Zebrafa Studióval").
        for i, v in enumerate(verdict):
            if not v:
                continue
            if out and out[-1][0] == v and i > 0 and verdict[i - 1] == v and joined(i - 1, i):
                out[-1] = (v, out[-1][1], tokens[i][1])
            else:
                out.append((v, tokens[i][0], tokens[i][1]))

        # Numbers, codes, dates, URLs
        for m in URL_RE.finditer(text):
            out.append(("URL", m.start(), m.end()))
        for m in DATE_RE.finditer(text):
            year = int(m.group("y1") or m.group("y2"))
            if year <= self.max_birth_year and free(m.start(), m.end()):
                out.append(("PAST_DATE", m.start(), m.end()))
        for m in DIGITS_RE.finditer(text):
            if sum(c.isdigit() for c in m.group()) >= 6 and free(m.start(), m.end()) and not DATE_RE.fullmatch(m.group().rstrip(".")):
                out.append(("NUMBER", m.start(), m.end()))
        for m in CODE_RE.finditer(text):
            g = SUFFIX_AFTER_NUMBER.sub("", m.group())  # "30-kor", "2026-os", "T1041-es" -> "T1041"
            if (len(re.sub(r"\W", "", g)) >= 5 and any(c.isdigit() for c in g) and any(c.isalpha() for c in g)
                    and free(m.start(), m.end())):
                out.append(("CODE", m.start(), m.end()))
        return [RecognizerResult(t, s, e, SCORE) for t, s, e in out]


def low_i(word: str) -> bool:
    return len(word) >= 4 and word.endswith("i") and word[:-1].isalpha()


def _skip_space(text: str, pos: int, step: int) -> int:
    """Position of the neighbouring span edge across one space."""
    if step < 0:
        return pos - 1 if pos > 0 and text[pos - 1] == " " else pos
    return pos + 1 if pos < len(text) and text[pos] == " " else pos
