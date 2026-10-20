"""Place names in any Hungarian case form, without a statistical model.

Sources (lexicon directory, see fetch_lexicon.py):

* ``settlements.txt``  Hungarian settlements (KSH gazetteer, optional)
* ``countries.txt``    country and territory names in Hungarian (Unicode CLDR)
* built-in: Hungarian names of frequently mentioned foreign cities and
  regions ("Bécs", "Pozsony", "Kolozsvár", "Erdély") and Budapest districts

Inflected forms are found with the rule-based morphology ("Debrecenben",
"Kiskunfélegyházán", "Németországba", "Egyesült Államokban") plus the old
locative ending ("Pécsett", "Győrött"). A place name is not personal data by
itself, but next to a person it narrows down who is meant.

Precision rules: only capitalised words; at the start of a sentence a name
that is also an ordinary word ("Hatvan perc múlva", "Pápa") is skipped; a
name followed by a street word belongs to the street ("Kossuth utca").
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult

from privacy_hub.masking.hungarian import HungarianMorphology

LETTER = "A-Za-zÀ-ÖØ-öø-ÿĀ-ſ"
TOKEN_RE = re.compile(rf"[{LETTER}][{LETTER}'’\-]*")
# old locative: Pécsett, Győrött, Vácott, Kaposvárott, Szegedt (rare)
LOCATIVE = re.compile(r"^(.{2,}?)(?:ott|ett|ött|t)$")
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
# Budapest districts ("XI. kerület" is handled by the address patterns) and frequent quarters
QUARTERS = {
    "Óbuda", "Újpest", "Kispest", "Pesterzsébet", "Csepel", "Budafok", "Rákospalota", "Zugló", "Angyalföld",
    "Ferencváros", "Józsefváros", "Erzsébetváros", "Terézváros", "Lipótváros", "Kőbánya", "Rákosmente",
    "Pestszentlőrinc", "Rózsadomb", "Pasarét", "Budagyöngye", "Kelenföld", "Lágymányos", "Csillaghegy",
    "Békásmegyer", "Mátyásföld", "Rákoskeresztúr", "Rákosliget", "Pestszentimre", "Soroksár", "Albertfalva",
}
# Places that are also ordinary words or given names (checked even without a word list)
AMBIGUOUS = {
    "Hatvan", "Baja", "Tata", "Pápa", "Mór", "Gyula", "Kaba", "Tét", "Ózd", "Eger", "Vác", "Sár", "Kál",
    "Ság", "Szentes", "Tolna", "Komló", "Barcs", "Csorna", "Paks", "Ajka", "Martonvásár", "Alsó", "Felső",
    "Kis", "Nagy", "Új", "Fót", "Bábolna", "Ács", "Kisvárda", "Abony", "Ócsa", "Tura", "Pér", "Kakucs",
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
        names = _load(directory / "settlements.txt") | _load(directory / "countries.txt") | EXONYMS | QUARTERS
        self.single = {n for n in names if " " not in n}
        # multi-word names by their last word: "Államok" -> {("Egyesült", "Államok"), ...}
        self.multi: dict[str, list[tuple[str, ...]]] = {}
        for n in names:
            words = tuple(n.split())
            if len(words) > 1:
                self.multi.setdefault(words[-1], []).append(words)
        self.common = {w.lower() for w in _load(directory / "common_words.txt")}
        self.has_settlements = (directory / "settlements.txt").exists()
        self.morph = HungarianMorphology(lexicon=self.single | set(self.multi))
        super().__init__(supported_entities=[self.ENTITY], supported_language=supported_language, name="HuPlaceRecognizer")

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
        before = text[:start].rstrip()
        return not before or before[-1] in ".!?:\n\"„(–-"

    def analyze(self, text, entities, nlp_artifacts=None):  # noqa: ARG002
        tokens = [(m.start(), m.end(), m.group(0)) for m in TOKEN_RE.finditer(text)]
        results: list[RecognizerResult] = []
        i = 0
        while i < len(tokens):
            start, end, w = tokens[i]
            if not w[:1].isupper():
                i += 1
                continue
            hit = self._multi_word(text, tokens, i)
            if hit:
                results.append(self._result(start, hit[1], 0.55, "multi-word place name"))
                i = hit[0]
                continue
            base = self._base(w, self.single)
            if base and not STREET_AFTER.match(text[end:]) and not self._ambiguous(text, start, w, base):
                results.append(self._result(start, end, 0.5, "place name"))
            i += 1
        return results

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
