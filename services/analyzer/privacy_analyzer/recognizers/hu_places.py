"""Place names in any Hungarian case form, without a statistical model.

Sources (lexicon directory, see fetch_lexicon.py):

* ``settlements.txt``  Hungarian settlements (KSH gazetteer, optional)
* ``countries.txt``    country and territory names in Hungarian (Unicode CLDR)
* ``foreign_cities.txt`` European cities (GeoNames), without names that are
  Hungarian words or given names ("Most", "Van", "Ede")
* built-in: Hungarian names of frequently mentioned foreign cities and
  regions ("Bécs", "Pozsony", "Kolozsvár", "Erdély") and Budapest districts
* hyphenated geographic names: "Hajógyári-szigeten", "Gellért-hegyre",
  "Velencei-tavon", "Duna-parton", "Margit-hídon"

Inflected forms are found with the rule-based morphology ("Debrecenben",
"Kiskunfélegyházán", "Németországba", "Egyesült Államokban") plus the old
locative ending ("Pécsett", "Győrött"). A place name is not personal data by
itself, but next to a person it narrows down who is meant.

Precision rules: only capitalised words; at the start of a sentence a name
that is also an ordinary word ("Hatvan perc múlva", "Pápa") is skipped; a
name followed by a street word belongs to the street ("Kossuth utca").

Names typed without accents ("Szolnokrol", "Budaorsre"; OCR output loses
accents the same way) are matched on their accent-less form, only inside a
sentence and only when the word is not an ordinary word once its accents
are gone. Lower-case place names in chat style ("kisteleken", "pesten",
"debrecenbe") need a case ending and must not be ordinary words.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from .hu_names import _plain, plain_stems

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult

from privacy_hub.masking.hungarian import HungarianMorphology

LETTER = "A-Za-zÀ-ÖØ-öø-ÿ\u0100-\u024f\u1e00-\u1eff"
TOKEN_RE = re.compile(rf"[{LETTER}][{LETTER}'’\-]*")
# old locative: Pécsett, Győrött, Vácott, Kaposvárott, Szegedt (rare)
LOCATIVE = re.compile(r"^(.{2,}?)(?:ott|ett|ött|t)$")
# Hyphenated geographic names with a case ending
GEO_COMPOUND = re.compile(
    r"^[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]+(?:-[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]+)?-"
    r"(?:sziget|félsziget|hegy|hegység|völgy|tó|tav|patak|part|liget|domb|puszta|erdő|tető|szoros|öböl|forrás|"
    r"barlang|csatorna|láp|medence|felvidék|síkság|híd|hid)"
    r"(?:a|e|ja|je|i|on|en|ön|n|ra|re|ról|ről|ba|be|ban|ben|ból|ből|hoz|hez|höz|tól|től|nál|nél|ig|t|at|ot|et|"
    r"ért|ként|ja|nak|nek)?$"
)
# Lower-case institution named after a settlement: "a budai központi kerületi
# bíróságon", "a szegedi kórházban" (chat style; capitalised names are found
# by the institution recognizer)
LOCAL_INSTITUTION = re.compile(
    r"(?<![\w])(?P<adj>[a-záéíóöőúüű]{3,}i)\s+(?:[a-záéíóöőúüű]+\s+){0,3}?"
    r"(?:bíróság|járásbíróság|törvényszék|ítélőtábl|kórház|klinik|egyetem|főiskol|rendőrkapitányság|rendőrség|"
    r"kormányhivatal|ügyészség|tankerület|önkormányzat|gimnázium|iskol|óvod|bölcsőd)"
    r"[a-záéíóöőúüű]*(?![\w])"
)
# place and date of a letter: "Ajka, 2026. október 12." (even an ambiguous name)
DATE_LINE_AFTER = re.compile(r"^,\s*(?:19|20)\d{2}\.")
STREET_AFTER = re.compile(r"^\s+(?:utc|út\b|úto|útr|útj|tér|ter[eé]|körút|köz\b|sor\b|fasor|sétány|rakpart|u\.)", re.I)

EXONYMS = {
    "Bécs", "Pozsony", "Kassa", "Kolozsvár", "Nagyvárad", "Temesvár", "Arad", "Szabadka", "Újvidék", "Eszék",
    "Zágráb", "Belgrád", "Krakkó", "Varsó", "Prága", "Brünn", "München", "Berlin", "Párizs", "London",
    "Róma", "Milánó", "Velence", "Nápoly", "Firenze", "Madrid", "Barcelona", "Lisszabon", "Athén", "Moszkva",
    "Kijev", "Ungvár", "Munkács", "Beregszász", "Marosvásárhely", "Csíkszereda", "Székelyudvarhely",
    "Sepsiszentgyörgy", "Brassó", "Szeben", "Gyulafehérvár", "Komárom", "Révkomárom", "Dunaszerdahely",
    "Érsekújvár", "Rimaszombat", "Graz", "Grác", "Salzburg", "Linz", "Innsbruck", "Kismarton", "Zürich",
    "Genf", "Brüsszel", "Amszterdam", "Hága", "Koppenhága", "Stockholm", "Oslo", "Helsinki", "Dublin",
    "Edinburgh", "Isztambul", "Bukarest", "Szófia", "Ljubljana", "Erdély", "Felvidék",
    "Délvidék", "Kárpátalja", "Partium", "Burgenland", "Bajorország", "Tirol", "Toszkána", "Dalmácia",
    "Balaton", "Alföld", "Dunántúl", "Tiszántúl", "Mátra", "Bükk", "Őrség", "Hortobágy",
}
# Foreign city names that are common English words in Hungarian office text
# ("a Split funkció", "Nice to meet you", "Sale kampány")
ENGLISH_WORD_CITIES = {
    "Split", "Reading", "Sale", "Nice", "Bath", "Bury", "Hove", "Marl", "Gare", "Most", "Bolton", "Wells", "Deal",
    "March", "Street", "Hamm", "Mobile", "Orange", "Moody", "Derby", "Poole", "Ashford", "Crawley", "Slough",
}
# Counties and regions of Hungary (settlement and country lists do not have them)
REGIONS = {
    "Baranya", "Bács-Kiskun", "Békés", "Borsod", "Borsod-Abaúj-Zemplén", "Csongrád-Csanád", "Fejér",
    "Győr-Moson-Sopron", "Hajdú-Bihar", "Heves", "Jász-Nagykun-Szolnok", "Komárom-Esztergom", "Nógrád",
    "Somogy", "Szabolcs", "Szabolcs-Szatmár-Bereg", "Tolna", "Vas", "Zala", "Bakony", "Mecsek", "Zemplén",
    "Jászság", "Kiskunság", "Nagykunság", "Nyírség", "Hajdúság", "Göcsej", "Ormánság", "Sárköz", "Szigetköz",
    "Csallóköz", "Bodrogköz", "Rétköz", "Cserehát", "Palócföld", "Börzsöny", "Pilis", "Vértes", "Gerecse",
    "Mezőföld", "Hanság", "Dunakanyar", "Tokaj-Hegyalja", "Székelyföld", "Muravidék", "Bácska", "Bánát",
}
# Budapest districts ("XI. kerület" is handled by the address patterns) and frequent quarters
QUARTERS = {
    "Budapest", "Pest", "Buda", "Óbuda", "Újpest", "Kispest", "Pesterzsébet", "Csepel", "Budafok", "Rákospalota", "Zugló", "Angyalföld",
    "Ferencváros", "Józsefváros", "Erzsébetváros", "Terézváros", "Lipótváros", "Kőbánya", "Rákosmente",
    "Pestszentlőrinc", "Rózsadomb", "Pasarét", "Budagyöngye", "Kelenföld", "Lágymányos", "Csillaghegy",
    "Békásmegyer", "Mátyásföld", "Rákoskeresztúr", "Rákosliget", "Pestszentimre", "Soroksár", "Albertfalva",
}
# Places that are also ordinary words or given names (checked even without a word list)
AMBIGUOUS = {
    "Hatvan", "Baja", "Tata", "Pápa", "Mór", "Gyula", "Kaba", "Tét", "Ózd", "Eger", "Vác", "Sár", "Kál",
    "Ság", "Szentes", "Tolna", "Komló", "Barcs", "Csorna", "Paks", "Ajka", "Martonvásár", "Alsó", "Felső",
    "Kis", "Nagy", "Új", "Fót", "Bábolna", "Ács", "Kisvárda", "Abony", "Ócsa", "Tura", "Pér", "Kakucs",
    "Vas", "Heves", "Pilis", "Békés",
}


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _load(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {_nfc(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


class HuPlaceRecognizer(EntityRecognizer):
    ENTITY = "LOCATION"

    def __init__(self, lexicon_dir: str | None = None, supported_language: str = "hu") -> None:
        directory = Path(lexicon_dir or os.environ.get("ANALYZER_LEXICON_DIR", "/app/data/lexicon"))
        self.common = {w.lower() for w in _load(directory / "common_words.txt")}
        given = _load(directory / "given_male.txt") | _load(directory / "given_female.txt")
        foreign = {c for c in _load(directory / "foreign_cities.txt")
                   if len(c) >= 4 and c.lower() not in self.common and c not in given}
        self.settlements = _load(directory / "settlements.txt") | QUARTERS
        names = self.settlements | _load(directory / "countries.txt") | EXONYMS | REGIONS | foreign
        self.single = {n for n in names if " " not in n}
        # multi-word names by their last word: "Államok" -> {("Egyesült", "Államok"), ...}
        self.multi: dict[str, list[tuple[str, ...]]] = {}
        for n in names:
            words = tuple(n.split())
            if len(words) > 1:
                self.multi.setdefault(words[-1], []).append(words)
        self.plain_common = {_plain(w) for w in self.common}
        # accent-less single-word names -> canonical name ("szolnok" -> "Szolnok")
        self.plain_single: dict[str, str] = {}
        for n in sorted(self.single):
            self.plain_single.setdefault(_plain(n), n)
        self.has_settlements = (directory / "settlements.txt").exists()
        # cheap pre-filter for lower-case words: accent-less first letters of a settlement name
        self._lower_prefixes = {_plain(n)[:5] for n in self.settlements if len(n) >= 4}  # "Pest" -> "pest"
        self.morph = HungarianMorphology(lexicon=self.single | set(self.multi))
        super().__init__(supported_entities=[self.ENTITY, "ORGANIZATION"], supported_language=supported_language,
                         name="HuPlaceRecognizer")

    def load(self) -> None:
        pass

    def _base(self, token: str, names: set[str]) -> str | None:
        if token in names:
            return token
        stem = self.morph.split_word(token)[0]
        if stem != token and stem in names:
            return stem
        m = LOCATIVE.match(token)
        if m and m.group(1) in names:
            return m.group(1)
        return None

    @staticmethod
    def _sentence_initial(text: str, start: int) -> bool:
        before = text[max(0, start - 200) : start].rstrip()  # bounded: see HuNameRecognizer
        if not before and start > 200:
            before = text[:start].rstrip()
        return not before or before[-1] in ".!?:\n\"„(–-"

    def analyze(self, text, entities, nlp_artifacts=None):  # noqa: ARG002
        tokens = [(m.start(), m.end(), m.group(0)) for m in TOKEN_RE.finditer(text) if len(m.group(0)) <= 40]
        results: list[RecognizerResult] = []
        i = 0
        while i < len(tokens):
            start, end, w = tokens[i]
            if not w[:1].isupper():
                if self._lower_place(text, start, w) and not STREET_AFTER.match(text[end:]):
                    results.append(self._result(start, end, 0.4, "lower-case place name"))
                i += 1
                continue
            if GEO_COMPOUND.match(w):
                results.append(self._result(start, end, 0.5, "geographic name"))
                i += 1
                continue
            hit = self._multi_word(text, tokens, i)
            if hit:
                results.append(self._result(start, hit[1], 0.55, "multi-word place name"))
                i = hit[0]
                continue
            base = self._base(w, self.single)
            dated = bool(base) and DATE_LINE_AFTER.match(text[end:]) is not None
            if base in ENGLISH_WORD_CITIES and base == w:
                base = None  # "a Split funkció": only an inflected form is the city ("Splitben")
            if base and not STREET_AFTER.match(text[end:]) and (dated or not self._ambiguous(text, start, w, base)):
                results.append(self._result(start, end, 0.5, "place name"))
            elif not base and w not in ENGLISH_WORD_CITIES and self._plain_place(text, start, w) and not STREET_AFTER.match(text[end:]):
                results.append(self._result(start, end, 0.45, "place name typed without accents"))
            i += 1
        results.extend(self._local_institutions(text))
        return results

    def _local_institutions(self, text: str) -> list[RecognizerResult]:
        out = []
        for m in LOCAL_INSTITUTION.finditer(text):
            stem = m.group("adj")[:-1]
            names = {stem[:1].upper() + stem[1:]}
            names |= {n + v for n in names for v in ("a", "e")}  # "nyíregyházi" -> "Nyíregyháza"
            if names & self.settlements:
                out.append(RecognizerResult("ORGANIZATION", m.start(), m.end(), 0.45, AnalysisExplanation(
                    recognizer=self.name, original_score=0.45, textual_explanation="institution named after a settlement")))
        return out

    def _multi_word(self, text, tokens, i):
        """'Egyesült Királyságban', 'Amerikai Egyesült Államokba'."""
        for j in range(i + 1, min(i + 4, len(tokens))):
            last = tokens[j][2]
            for words in self.multi.get(self._base(last, set(self.multi)) or "", []):
                if j - i + 1 != len(words):
                    continue
                seq = tokens[i : j + 1]
                if all(seq[k][2] == words[k] for k in range(len(words) - 1)) and all(
                    text[seq[k][1] : seq[k + 1][0]] == " " for k in range(len(seq) - 1)
                ):
                    return j + 1, tokens[j][1]
        return None

    def _plain_place(self, text: str, start: int, token: str) -> bool:
        """'Szolnokrol', 'Budaorsre': an accent-less word inside a sentence
        whose accent-less base form is a known place and not an ordinary word."""
        if not token.isascii() or len(token) < 4 or self._sentence_initial(text, start):
            return False
        stems = plain_stems(token)
        if stems & self.plain_common:
            return False
        for stem in stems | {p[:-3] for p in stems if p.endswith(("ott", "ett"))}:
            name = self.plain_single.get(stem)
            if name and name not in AMBIGUOUS:
                return True
        return False

    def _common_word(self, token: str) -> bool:
        """An ordinary word, also inflected (lemma of 5+ letters + ending)."""
        low = token.lower()
        if low in self.common:
            return True
        return any(low[:k] in self.common for k in range(len(low) - 1, max(4, len(low) - 8), -1))

    def _lower_place(self, text: str, start: int, token: str) -> bool:
        """Chat style 'kisteleken', 'pesten', 'debrecenbe', 'kecskemetrol': a
        Hungarian settlement written in lower case with a case ending, whose
        name is not an ordinary word ("szentesen", "üllőn" are skipped)."""
        if len(token) < 5 or not token.islower():
            return False
        p = _plain(token)
        if p[:5] not in self._lower_prefixes and p[:4] not in self._lower_prefixes:
            return False
        cap = token[:1].upper() + token[1:]
        stem = self.morph.split_word(cap)[0]
        m = LOCATIVE.match(cap)
        candidates = {stem} | ({m.group(1)} if m else set())
        if not token.isascii() or not (candidates & self.settlements):
            plain = {self.plain_single.get(p) for p in plain_stems(token) if p != _plain(token)} if token.isascii() else set()
            candidates |= {p for p in plain if p}
        bases = {c for c in candidates if c != cap and c in self.settlements and len(c) >= 4 and c not in AMBIGUOUS}
        if not bases:
            return False
        if any(b.lower() in self.common or _plain(b) in self.plain_common for b in bases):
            return False
        return not self._common_word(token)

    def _ambiguous(self, text: str, start: int, token: str, base: str) -> bool:
        """At the start of a sentence, a place name that is also an ordinary
        word or given name is not trusted ("Hatvan perc múlva", "Gyula írt")."""
        if not self._sentence_initial(text, start):
            return False
        return base in AMBIGUOUS or base.lower() in self.common or token.lower() in self.common

    def _result(self, start: int, end: int, score: float, why: str) -> RecognizerResult:
        return RecognizerResult(
            self.ENTITY, start, end, score, AnalysisExplanation(recognizer=self.name, original_score=score, textual_explanation=why)
        )
