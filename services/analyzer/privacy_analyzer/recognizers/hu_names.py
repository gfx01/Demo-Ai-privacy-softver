"""Gazetteer-based Hungarian person-name recognizer.

Uses the official list of registrable Hungarian given names, common
nicknames, frequent surnames and common foreign given names (see
fetch_lexicon.py) together with Hungarian name order (surname first) and the
rule-based morphology, so inflected forms are found:

* "Kovács Péterrel", "Évának", "dr. Tóth Gáborral"
* married names: "Kovácsné Tóth Éva", "Fehér Andrásné", "Kissnének"
* family forms: "Kovácsékkal", "Szűcséknek"
* a known surname inflected mid-sentence: "Kissnek szóltam"
* foreign names inside multi-word runs: "Thomas Becker", "Becker Thomast"
* lower-case chat style only in greetings ("szia peti") or as a known
  surname + given name pair ("horváth tamás")

Works without any statistical NER model and complements one when present.
Known gaps: other lower-case names, ambiguous given names that are common
words at the start of a sentence ("Rózsa", "Virág"), initials ("K. Péter").
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult

from privacy_hub.masking.hungarian import HungarianMorphology

TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿŐőŰű][A-Za-zÀ-ÖØ-öø-ÿŐőŰű'’\-]*")
TITLE_WORDS = {"dr", "ifj", "id", "özv", "prof", "néhai", "vitéz", "mr", "mrs", "ms", "miss", "sir", "herr", "frau"}
TITLE_BEFORE = re.compile(r"(?:\b(?:dr|ifj|id|özv|prof|mr|mrs|ms)\.?)\s*$", re.I)
HONORIFICS = re.compile(r"^\s+(?:úr|úrnak|urat|úrral|úrtól|úrhoz|urak|asszony|asszonynak|asszonyt|asszonnyal|"
                        r"kisasszony|doktor|doktornő|doktorúr|ügyvéd|ügyvédnő|tanár|tanárnő|néni|bácsi)\b", re.I)
GREETING = re.compile(r"(?:^|[\s,(])(?:szia|szió|sziasztok|helló|hello|hali|hi|hey|kedves|üdv|szervusz|csá|csao|ciao)[\s,!]*$", re.I)
# Words after a name that turn it into a street / institution name.
# (Prefix match: the street word itself may be inflected, "utcában", "téren".)
NOT_A_PERSON_AFTER = re.compile(
    r"^\s*-?(?:utc[aá]|útj|út\b|úto|úti|tér|ter[eé]|körút|köz\b|köz[eéb]|sor\b|sor[aáo]|fasor|sétány|"
    r"rakpart|park\b|park[ob]|liget|híd|hídon|gimnázium|általános|iskol|kórház|egyetem|kollégium|intézet|"
    r"alapítvány|társaság|díj|emlékérem|kft|zrt|bt\b|nyrt|kkt|ügyvédi|u\.)",
    re.I,
)
# Capitalised words that start sentences or greetings but are not surnames.
STOPWORDS = {
    "A", "Az", "Egy", "Ma", "Tegnap", "Holnap", "Kedves", "Tisztelt", "Kérem", "Kérlek", "Szia", "Sziasztok",
    "Szervusz", "Helló", "Hello", "Hi", "Üdv", "Üdvözlettel", "Köszönöm", "Köszönjük", "Ezért", "Továbbá",
    "Valamint", "Mivel", "Hogy", "Ha", "Mert", "És", "De", "Sajnos", "Remélem", "Amikor", "Mint", "Ön", "Önök",
    "Re", "Fw", "Tárgy", "Ügyfél", "Partner", "Megrendelő", "Vállalkozó", "Bérlő", "Bérbeadó", "Eladó", "Vevő",
    "Beteg", "Páciens", "Panaszos", "Felperes", "Alperes", "Meghatalmazó", "Meghatalmazott", "Munkavállaló",
    "Munkáltató", "Név", "Neve", "Írj", "Foglald", "Fogalmazz", "Készíts", "Segíts", "Nézd", "Ellenőrizd",
    "Javítsd", "Fordítsd", "Válaszolj", "Küldd", "Hívd", "Szerda", "Hétfő", "Kedd", "Csütörtök", "Péntek",
    "Szombat", "Vasárnap", "Január", "Február", "Március", "Április", "Május", "Június", "Július", "Augusztus",
    "Szeptember", "Október", "November", "December", "Budapest", "Magyarország", "Debrecen", "Szeged", "Pécs",
    "Győr", "Miskolc", "Utca", "Cím", "Telefon", "Email", "Tel", "Mobil", "Dátum", "Ügyszám", "Iktatószám",
    "Dear", "Hallo", "Liebe", "Lieber", "Sehr", "Mr", "Mrs", "Ms", "Dr",
}
# Registrable given names that are also common words; on their own and at
# the start of a sentence they are not treated as names.
AMBIGUOUS_GIVEN = {
    "Alma", "Arany", "Áron", "Barna", "Bátor", "Bodza", "Boróka", "Bíbor", "Csillag", "Gyöngy", "Győző",
    "Hajnal", "Hanga", "Ibolya", "Jácint", "Kamilla", "Kincs", "Liliom", "Menta", "Napsugár", "Nemes",
    "Remény", "Rózsa", "Szende", "Szilárd", "Tavasz", "Viola", "Virág", "Vidor", "Vitéz", "Zsálya", "Mária",
    "Dalma", "Ernő", "Kende", "Hunor", "Bende", "Kis", "Nagy", "Fehér", "Fekete",
}
# Common Hungarian nicknames (most are not on the official list).
NICKNAMES = {
    "Peti", "Pisti", "Laci", "Feri", "Józsi", "Jóska", "Jani", "Janika", "Zoli", "Gabi", "Tomi", "Andris", "Dani",
    "Gergő", "Misi", "Miki", "Robi", "Zsolti", "Csabi", "Ricsi", "Krisz", "Kriszti", "Zsuzsi", "Kati", "Erzsi",
    "Marcsi", "Juci", "Ili", "Ica", "Bori", "Évi", "Ani", "Anni", "Panni", "Fruzsi", "Eszti", "Moni", "Niki",
    "Viki", "Dóri", "Rebi", "Nóri", "Szandi", "Bogi", "Reni", "Orsi", "Timi", "Klári", "Gizi", "Juli", "Lali",
    "Bandi", "Sanyi", "Pityu", "Öcsi", "Balu", "Gazsi", "Lacika", "Ferike", "Petike", "Zsófi", "Dorka", "Zsuzsa",
    "Marika", "Ági", "Évike", "Kata", "Detti", "Betti", "Pali", "Tibi", "Gyuri", "Matyi", "Marci", "Barni",
    "Levi", "Ádi", "Bazsi", "Imi", "Karcsi", "Lajcsi", "Norbi", "Szabi", "Vili",
}


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _load(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {_nfc(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


class HuNameRecognizer(EntityRecognizer):
    ENTITY = "PERSON"

    def __init__(self, lexicon_dir: str | None = None, supported_language: str = "hu") -> None:
        directory = Path(lexicon_dir or os.environ.get("ANALYZER_LEXICON_DIR", "/app/data/lexicon"))
        self.male = _load(directory / "given_male.txt")
        self.female = _load(directory / "given_female.txt")
        if not self.male:
            raise RuntimeError(f"Hungarian given-name list not found in {directory}")
        self.given = self.male | self.female | NICKNAMES
        self.surnames = _load(directory / "surnames.txt")
        self.foreign = _load(directory / "foreign_given.txt") - STOPWORDS
        self.morph = HungarianMorphology(lexicon=self.given | self.surnames)
        super().__init__(supported_entities=[self.ENTITY], supported_language=supported_language, name="HuNameRecognizer")

    def load(self) -> None:
        pass

    # -- token classification -------------------------------------------------
    def _stem(self, token: str) -> tuple[str, str | None]:
        stem, case, _ = self.morph.split_word(token)
        return (stem, case) if case else (token, None)

    def _married_base(self, token: str) -> str | None:
        """'Andrásné' / 'Lászlónéhoz' / 'Kissnének' -> 'András' / 'László' / 'Kiss'."""
        m = re.match(r"^(.{3,}?)né(?:nek|nél|hez|vel|t|ről|től|re|é|ként)?$", token)
        return m.group(1) if m else None

    def is_given(self, token: str) -> bool:
        if token in self.given or self._stem(token)[0] in self.given:
            return True
        base = self._married_base(token)
        return base is not None and base in self.male

    def is_known_surname(self, token: str) -> bool:
        stem = self._stem(token)[0]
        base = self._married_base(token) or stem
        family = re.match(r"^(.+?)ék", token)
        parts = {token, stem, base, *base.split("-")} | ({family.group(1)} if family else set())
        return any(p in self.surnames or self._stem(p)[0] in self.surnames for p in parts)

    def _family_form(self, token: str) -> bool:
        m = re.match(r"^(.+?)ék(?:[a-záéíóöőúüű]{0,6})$", token)
        return bool(m) and (m.group(1) in self.surnames or m.group(1).capitalize() in self.surnames)

    @staticmethod
    def _capitalised(token: str) -> bool:
        return token[:1].isupper() and (not token.isupper() or len(token) == 1)

    @staticmethod
    def _sentence_initial(text: str, start: int) -> bool:
        before = text[:start].rstrip()
        if not before:
            return True
        if before[-1] == "." and TITLE_BEFORE.search(before):
            return False  # "dr. Bíró Éva": the dot belongs to the title
        return before[-1] in ".!?:\n\"„(" or before.endswith(("–", "-"))

    # -- analysis -------------------------------------------------------------
    def analyze(self, text, entities, nlp_artifacts=None):  # noqa: ARG002
        tokens = [(m.start(), m.end(), m.group(0)) for m in TOKEN_RE.finditer(text)]
        results: list[RecognizerResult] = []
        i = 0
        while i < len(tokens):
            if not self._capitalised(tokens[i][2]):
                found = self._lowercase_name(text, tokens, i)
                if found:
                    results.append(found[0])
                    i = found[1]
                else:
                    i += 1
                continue
            run = [i]
            j = i
            while (
                j + 1 < len(tokens)
                and len(run) < 5
                and text[tokens[j][1] : tokens[j + 1][0]] == " "
                and self._capitalised(tokens[j + 1][2])
            ):
                j += 1
                run.append(j)
            found = self._name_in_run(text, tokens, run)
            if found:
                results.append(found)
            i = j + 1
        taken = [(r.start, r.end) for r in results]
        results.extend(self._single_surnames(text, tokens, taken))
        return results

    def _result(self, start: int, end: int, score: float, why: str) -> RecognizerResult:
        return RecognizerResult(
            self.ENTITY, start, end, score, AnalysisExplanation(recognizer=self.name, original_score=score, textual_explanation=why)
        )

    def _name_in_run(self, text, tokens, run):
        words = [tokens[k][2] for k in run]
        given_idx = [n for n, w in enumerate(words) if self.is_given(w)]
        if not given_idx and len(words) >= 2:
            # "Thomas Becker", "Becker Thomast": foreign given names only in runs
            given_idx = [n for n, w in enumerate(words) if w in self.foreign or self._stem(w)[0] in self.foreign]
        if not given_idx:
            return None
        last_given = given_idx[-1]
        first = 0
        while first < last_given:
            w = words[first]
            start = tokens[run[first]][0]
            if w in STOPWORDS or w.lower().rstrip(".") in TITLE_WORDS:
                first += 1
                continue
            if self._sentence_initial(text, start) and not (
                self.is_known_surname(w) or self.is_given(w) or w.endswith("né") or w in self.foreign
            ):
                first += 1
                continue
            break
        end_idx = last_given
        # Western order "Péter Kovács" / "Thomas Becker": a surname-like word after the given name
        if end_idx + 1 < len(words) and first == end_idx and words[end_idx + 1] not in STOPWORDS and (
            self.is_known_surname(words[end_idx + 1]) or words[end_idx] in self.foreign
        ):
            end_idx += 1
        span_start = tokens[run[first]][0]
        span_end = tokens[run[end_idx]][1]
        name_words = words[first : end_idx + 1]
        if NOT_A_PERSON_AFTER.match(text[span_end:]):
            return None
        if len(name_words) == 1:
            w = name_words[0]
            if w in STOPWORDS or not self.is_given(w):
                return None
            stem = self._stem(w)[0]
            if (w in AMBIGUOUS_GIVEN or stem in AMBIGUOUS_GIVEN) and self._sentence_initial(text, span_start):
                return None
            score, why = 0.6, "given name alone"
        elif any(self.is_known_surname(w) for w in name_words if not self.is_given(w)) or all(
            self.is_given(w) for w in name_words
        ):
            score, why = 0.85, "surname + given name"
        else:
            score, why = 0.7, "capitalised word + given name"
        if TITLE_BEFORE.search(text[max(0, span_start - 6) : span_start]):
            score = min(1.0, score + 0.1)
        return self._result(span_start, span_end, score, why)

    def _lowercase_name(self, text, tokens, i):
        """Chat style: 'szia peti', 'horváth tamással'."""
        start, end, w = tokens[i]
        cap = w[:1].upper() + w[1:]
        if i + 1 < len(tokens) and text[end : tokens[i + 1][0]] == " ":
            nxt = tokens[i + 1][2]
            nxt_cap = nxt[:1].upper() + nxt[1:]
            if self.is_known_surname(cap) and self.is_given(nxt_cap) and cap not in AMBIGUOUS_GIVEN:
                return self._result(start, tokens[i + 1][1], 0.6, "lower-case surname + given name"), i + 2
        if len(w) >= 3 and cap not in AMBIGUOUS_GIVEN and (self.is_given(cap) or self._family_form(cap)):
            if GREETING.search(text[max(0, start - 25) : start]):
                return self._result(start, end, 0.5, "lower-case name after greeting"), i + 1
        if len(w) >= 6 and self._family_form(cap):
            return self._result(start, end, 0.5, "lower-case family form"), i + 1
        return None

    def _single_surnames(self, text, tokens, taken):
        """Known surnames standing alone: with a title/honorific, inflected
        mid-sentence ("Kissnek"), as family form ("Kovácsékkal") or as a
        married-name form ("Kissné")."""
        out = []
        for start, end, w in tokens:
            if any(s <= start < e for s, e in taken) or not self._capitalised(w) or w in STOPWORDS:
                continue
            if not self.is_known_surname(w):
                continue
            initial = self._sentence_initial(text, start)
            stem, case = self._stem(w)
            if HONORIFICS.match(text[end:]) or TITLE_BEFORE.search(text[max(0, start - 6) : start]):
                out.append(self._result(start, end, 0.6, "surname + title"))
            elif self._family_form(w) or self._married_base(w):
                out.append(self._result(start, end, 0.6, "family / married-name form"))
            elif case and not initial and w not in AMBIGUOUS_GIVEN and stem not in AMBIGUOUS_GIVEN:
                out.append(self._result(start, end, 0.5, "inflected surname"))
        return out
