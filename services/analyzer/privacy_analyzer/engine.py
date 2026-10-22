"""Presidio analyzer + anonymizer wiring.

Detection: one Presidio ``AnalyzerEngine`` per language, each with its own
pluggable NLP/NER backend, plus custom recognizers (Hungarian identifiers,
gazetteer names). Replacement: ``replace_spans`` (the behaviour of Presidio's
``AnonymizerEngine`` with a ``custom`` operator, in linear time) delegating to
``privacy_hub``'s ``MaskingSession`` (placeholder strategy + Hungarian suffix
handling).

Presidio's duplicate removal and the anonymizer's conflict check compare
every pair of results; a long text with thousands of entities (or crafted
input) then took minutes, holding the GIL. ``fast_remove_duplicates`` keeps
Presidio's semantics in O(n log n) and is installed in its place.

Hungarian NER backend (env ANALYZER_HU_NER):
  none                     no statistical NER (blank Hungarian tokenizer);
                           names come from the gazetteer recognizer only
  spacy:<name-or-path>     any spaCy pipeline with an NER component, e.g.
                           spacy:hu_core_news_md (HuSpaCy), spacy:xx_ent_wiki_sm,
                           spacy:/app/models/hu_nerkor_cnn (scripts/train_nerkor.py)
  transformers:<hf-model>  Presidio TransformersNlpEngine (needs torch +
                           transformers in the image; not tested in this repo)
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path

import tldextract
from presidio_analyzer import AnalysisExplanation, AnalyzerEngine, EntityRecognizer, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NerModelConfiguration, NlpEngineProvider

from privacy_hub.masking import MaskingSession, MaskResult, Span, get_morphology, resolve_overlaps
from privacy_hub.masking.pseudonyms import PseudonymStrategy

from .maximum import MaximumDetector
from .recognizers import AnyTldEmailRecognizer, GuardedPhoneRecognizer, HuNameRecognizer, hungarian_recognizers

log = logging.getLogger("privacy_analyzer.engine")

# Presidio's EmailRecognizer calls tldextract.extract(), which downloads the
# public suffix list on first use. The analyzer runs without network egress,
# so use tldextract's bundled snapshot instead (no network, no cache dir).
tldextract.extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

# Entity types that are detected but not masked by default (too noisy / not
# personal data on their own). Organisations can change this later.
DEFAULT_IGNORED = {"DATE_TIME", "NRP", "URL", "IN_PAN", "US_DRIVER_LICENSE", "US_BANK_NUMBER"}
# Detection levels: "maximum" adds MaximumDetector (Hungarian only) and masks URLs.
DETECTION_LEVELS = ("standard", "maximum")

NER_MAPPING = {
    "PER": "PERSON",
    "PERSON": "PERSON",
    "LOC": "LOCATION",
    "GPE": "LOCATION",
    "FAC": "LOCATION",
    "ORG": "ORGANIZATION",
    "ORGANIZATION": "ORGANIZATION",
    "NORP": "NRP",
    "DATE": "DATE_TIME",
    "TIME": "DATE_TIME",
}
NER_RECOGNIZERS = {"SpacyRecognizer", "TransformersRecognizer", "StanzaRecognizer"}
NER_IGNORE = ["MISC", "CARDINAL", "ORDINAL", "QUANTITY", "PERCENT", "WORK_OF_ART", "LANGUAGE", "EVENT",
              "PRODUCT", "LAW", "MONEY"]


# Invisible characters that are not format characters (category Cf):
# variation selectors, combining grapheme joiner, Hangul fillers.
_INVISIBLE_OTHER = frozenset(
    [*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0), 0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0]
)


def _visible(ch: str) -> bool:
    return unicodedata.category(ch) != "Cf" and ord(ch) not in _INVISIBLE_OTHER


def normalize_text(text: str) -> str:
    """NFC + removal of invisible characters (all format characters such as
    zero-width spaces/joiners, soft hyphen, bidi marks and isolates, word
    joiner, BOM, tag characters; plus variation selectors, CGJ and Hangul
    fillers). One of them inside "Kov<U+200E>ács" would otherwise hide the
    name from every recognizer while the model still reads "Kovács"."""
    text = unicodedata.normalize("NFC", text)
    if text.isascii():
        return text
    return "".join(ch for ch in text if _visible(ch))


# a line break that is not part of an empty line (paragraph breaks stay)
SOFT_LINE_BREAK = re.compile(r"(?<!\n)\n(?!\n)")


def fast_remove_duplicates(results: list[RecognizerResult]) -> list[RecognizerResult]:
    """Presidio's ``EntityRecognizer.remove_duplicates`` in O(n log n).

    Same rules: equal results (span, type, score) collapse to one; by
    descending score, then start, then length, a result is dropped when an
    already kept result of the same type contains its span; zero scores are
    dropped. Containment is a prefix-maximum query (a Fenwick tree over the
    start positions of each type) instead of a scan of every kept result."""
    unique: dict[tuple, RecognizerResult] = {}
    for r in results:
        unique.setdefault((r.start, r.end, r.score, r.entity_type), r)
    ordered = sorted(unique.values(), key=lambda x: (-x.score, x.start, -(x.end - x.start)))
    starts: dict[str, list[int]] = defaultdict(list)
    for r in ordered:
        starts[r.entity_type].append(r.start)
    index = {t: sorted(set(v)) for t, v in starts.items()}
    trees = {t: [-1] * (len(v) + 1) for t, v in index.items()}
    kept = []
    for r in ordered:
        if r.score == 0:
            continue
        pos, tree = index[r.entity_type], trees[r.entity_type]
        i, best = bisect_right(pos, r.start), -1  # max end of kept spans with start <= r.start
        while i > 0:
            best = max(best, tree[i])
            i -= i & -i
        if best >= r.end:
            continue  # contained in a kept result of the same type
        kept.append(r)
        i = bisect_left(pos, r.start) + 1
        while i < len(tree):
            tree[i] = max(tree[i], r.end)
            i += i & -i
    return kept


# Presidio calls it as EntityRecognizer.remove_duplicates(...) (AnalyzerEngine,
# PatternRecognizer); the replacement is checked against the original in tests.
PRESIDIO_REMOVE_DUPLICATES = EntityRecognizer.remove_duplicates
EntityRecognizer.remove_duplicates = staticmethod(fast_remove_duplicates)


def replace_spans(text: str, results: list[RecognizerResult], replacement) -> str:
    """What Presidio's ``AnonymizerEngine.anonymize`` does with a custom
    operator for non-overlapping results, in linear time: neighbours of the
    same type separated only by spaces are merged ("Kovács" + "Péter"), and
    the replacement function is called from the last entity to the first."""
    merged: list[list] = []
    for r in sorted(results, key=lambda x: (x.start, x.end)):
        if merged and merged[-1][2] == r.entity_type and _SPACES.search(text[merged[-1][1] : r.start]):
            merged[-1][1] = r.end
        else:
            merged.append([r.start, r.end, r.entity_type])
    out = []
    end = len(text)
    for start, stop, etype in reversed(merged):
        out.append(text[stop:end])
        out.append(replacement(etype, text[start:stop]))
        end = start
    out.append(text[:end])
    return "".join(reversed(out))


_SPACES = re.compile(r"^( )+$")  # exactly Presidio's test, including its "spaces + final newline" case

ORG_TYPES = ("HU_COMPANY", "ORGANIZATION")
# Hungarian endings after a company's short name: "Bitkerttel", "Mecsek-Vill-lel", "Alfának"
_ORG_SHORT_ENDING = (
    r"(?:-?(?:[bcdfghjklmnprstvz]?[ae]l|t|ot|et|at|öt|nak|nek|nál|nél|hoz|hez|höz|tól|től|ból|ből|ban|ben|ba|be|"
    r"ra|re|ról|ről|on|en|ön|n|ért|ként|é|nek|é))?"
)


def repeated_org_names(text: str, results: list[RecognizerResult], ignored: set[str]) -> list[RecognizerResult]:
    """The distinctive first word of a detected company or organisation name
    ("Bitkert Informatikai Zrt.") is masked again where it appears on its
    own, also inflected ("a Bitkerttel", "a Mecsek-Vill-lel")."""
    out: list[RecognizerResult] = []
    if ignored & set(ORG_TYPES):
        return out
    covered = bytearray(len(text) + 1)  # O(1) "already masked?" (a scan per match was quadratic)
    for r in results:
        covered[r.start : r.end] = b"\x01" * (r.end - r.start)
    for r in [r for r in results if r.entity_type in ORG_TYPES]:
        first = text[r.start : r.end].split()[0]
        if len(first) < 4 or not first[:1].isupper() or first in _ORG_COMMON_FIRST:
            continue
        for m in re.finditer(rf"(?<![\w-]){re.escape(first)}{_ORG_SHORT_ENDING}(?![\w])", text):
            if covered[m.start()]:
                continue
            covered[m.start() : m.end()] = b"\x01" * (m.end() - m.start())
            out.append(RecognizerResult(r.entity_type, m.start(), m.end(), 0.5,
                                        AnalysisExplanation(recognizer="RepeatedOrgName", original_score=0.5,
                                                            textual_explanation="short form of an organisation found in the same text")))
    return out


# first words of organisation names that are too generic to mask on their own
_ORG_COMMON_FIRST = {
    "Magyar", "Nemzeti", "Országos", "Állami", "Fővárosi", "Budapesti", "Központi", "Városi", "Megyei", "Első",
    "Általános", "Egyesült", "Szent", "Dél", "Észak", "Kelet", "Nyugat", "Közép", "Új", "Nagy", "Kis",
}


@dataclass
class EngineInfo:
    languages: list[str]
    nlp_backends: dict[str, str]
    load_seconds: float
    recognizers: dict[str, list[str]] = field(default_factory=dict)


def _blank_hungarian_path() -> str:
    import spacy

    path = tempfile.mkdtemp(prefix="hu_blank_")
    spacy.blank("hu").to_disk(path)
    return path


def _nlp_engine(lang: str, backend: str):
    ner_cfg = NerModelConfiguration(
        model_to_presidio_entity_mapping=NER_MAPPING,
        labels_to_ignore=NER_IGNORE,
        low_score_entity_names=[],
    )
    kind, _, target = backend.partition(":")
    if kind == "none":
        conf = {"nlp_engine_name": "spacy", "models": [{"lang_code": lang, "model_name": _blank_hungarian_path()}]}
    elif kind == "spacy":
        import spacy

        # Presidio would try spacy.cli.download() for a missing model at
        # runtime; the analyzer has no egress, and HuSpaCy is not in spaCy's
        # registry anyway. Fail with a clear message instead.
        if not (spacy.util.is_package(target) or Path(target).exists()):
            raise RuntimeError(f"NER model {target!r} is not installed in the analyzer image")
        conf = {"nlp_engine_name": "spacy", "models": [{"lang_code": lang, "model_name": target}]}
    elif kind == "transformers":
        conf = {
            "nlp_engine_name": "transformers",
            "models": [{"lang_code": lang, "model_name": {"spacy": _blank_hungarian_path(), "transformers": target}}],
        }
    else:
        raise ValueError(f"unknown NER backend {backend!r}")
    provider = NlpEngineProvider(nlp_configuration={**conf, "ner_model_configuration": ner_cfg.to_dict()})
    return provider.create_engine()


class PrivacyEngine:
    def __init__(
        self,
        languages: list[str] | None = None,
        hu_ner: str | None = None,
        en_ner: str | None = None,
        gazetteer: bool | None = None,
        lexicon_dir: str | None = None,
    ) -> None:
        started = time.perf_counter()
        self.languages = languages or [s.strip() for s in os.environ.get("ANALYZER_LANGUAGES", "hu,en").split(",") if s.strip()]
        backends = {
            "hu": hu_ner or os.environ.get("ANALYZER_HU_NER", "none"),
            "en": en_ner or os.environ.get("ANALYZER_EN_NER", "spacy:en_core_web_md"),
        }
        if gazetteer is None:
            gazetteer = os.environ.get("ANALYZER_HU_GAZETTEER", "true").lower() in {"1", "true", "yes"}
        self.nlp_backends = {lang: backends[lang] for lang in self.languages}
        self.analyzers: dict[str, AnalyzerEngine] = {}
        for lang in self.languages:
            nlp = _nlp_engine(lang, backends[lang])
            registry = RecognizerRegistry(supported_languages=[lang])
            registry.load_predefined_recognizers(languages=[lang], nlp_engine=nlp)
            if lang == "hu":
                # Keep only the NER recognizer of the NLP engine; everything
                # else is added explicitly so the Hungarian set is deterministic.
                for rec in list(registry.recognizers):
                    if rec.name not in NER_RECOGNIZERS:
                        registry.remove_recognizer(rec.name)
                for rec in hungarian_recognizers(gazetteer=gazetteer, lexicon_dir=lexicon_dir):
                    registry.add_recognizer(rec)
            else:
                registry.remove_recognizer("PhoneRecognizer")
                registry.remove_recognizer("EmailRecognizer")
                registry.add_recognizer(AnyTldEmailRecognizer(supported_language=lang))
                registry.add_recognizer(GuardedPhoneRecognizer(supported_regions=("HU", "US", "UK", "DE", "AT", "FR"), supported_language=lang))
            self.analyzers[lang] = AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=[lang])
        names = next((r for a in self.analyzers.values() for r in a.registry.recognizers if isinstance(r, HuNameRecognizer)), None)
        self._lexicon = (names.given | names.surnames) if names else set()
        self._male = names.male if names else set()
        self._female = names.female if names else set()
        self.maximum: MaximumDetector | None = None
        if "hu" in self.languages and gazetteer:
            try:
                self.maximum = MaximumDetector(lexicon_dir)
            except RuntimeError as exc:
                log.warning("maximum detection level unavailable: %s", exc)
        self.info = EngineInfo(
            self.languages,
            dict(self.nlp_backends),
            time.perf_counter() - started,
            {lang: sorted(r.name for r in a.registry.recognizers) for lang, a in self.analyzers.items()},
        )
        log.info("engine ready: languages=%s backends=%s load=%.1fs", self.languages, self.nlp_backends, self.info.load_seconds)

    def gender_of(self, given: str) -> str | None:
        stem = get_morphology("hu", self._lexicon).split_word(given)[0] if self._lexicon else given
        male, female = stem in self._male, stem in self._female
        return "male" if male and not female else "female" if female and not male else None

    def analyze(
        self,
        text: str,
        language: str,
        entities: list[str] | None = None,
        score_threshold: float = 0.4,
        disabled_entities: list[str] | tuple[str, ...] = (),
        detection: str = "standard",
    ) -> list[RecognizerResult]:
        if detection not in DETECTION_LEVELS:
            raise ValueError(f"unknown detection level {detection!r}")
        maximum = detection == "maximum" and language == "hu"
        if maximum and self.maximum is None:
            raise ValueError("the maximum detection level is not available (needs the gazetteer and word_forms.txt)")
        analyzer = self.analyzers[language]
        results = analyzer.analyze(text=text, language=language, entities=entities, score_threshold=score_threshold)
        # A line break inside a name, number or address (wrapped lines of OCR
        # output, signature and address blocks) hides it from most
        # recognizers: "Gál\nViktória", "HU54 5451 ... 8800\n5079 4192".
        # A copy with single line breaks turned into spaces has the same
        # length, so its spans map 1:1; whatever it adds is kept.
        view = SOFT_LINE_BREAK.sub(" ", text) if "\n" in text else text
        if view != text:
            results += analyzer.analyze(text=view, language=language, entities=entities, score_threshold=score_threshold)
        ignored = set(disabled_entities) | (DEFAULT_IGNORED - ({"URL"} if maximum else set()) if entities is None else set())
        results = [r for r in results if r.entity_type not in ignored]
        if language == "hu":
            results += repeated_org_names(text, results, ignored)
        if maximum:
            results += [r for r in self.maximum.detect(text, results) if r.entity_type not in ignored]
        # Resolve overlaps here (prefer higher score, then longer span) so that
        # pre-registration and Presidio's anonymizer see the same span set.
        keep = {
            (s.entity_type, s.start, s.end)
            for s in resolve_overlaps([Span(r.entity_type, r.start, r.end, r.score) for r in results])
        }
        unique: dict[tuple[str, int, int], RecognizerResult] = {}
        for r in results:
            k = (r.entity_type, r.start, r.end)
            if k in keep and (k not in unique or r.score > unique[k].score):
                unique[k] = r
        return sorted(unique.values(), key=lambda r: r.start)

    def mask(
        self,
        texts: list[str],
        language: str,
        strategy: str,
        entities: list[str] | None = None,
        score_threshold: float = 0.4,
        disabled_entities: list[str] | tuple[str, ...] = (),
        detection: str = "standard",
    ) -> MaskResult:
        if language not in self.analyzers:
            raise ValueError(f"language {language!r} is not loaded (available: {self.languages})")
        # Decomposed accents ("a" + U+0301) and invisible characters inside a
        # word would make names and numbers invisible to the recognizers. The
        # normalised text is what gets masked and sent on.
        texts = [normalize_text(t) for t in texts]
        strat = PseudonymStrategy(gender_of=self.gender_of) if strategy == "pseudonym" else strategy
        session = MaskingSession(strat, get_morphology(language, self._lexicon), existing_texts=texts, language=language)
        analyzed = [self.analyze(t, language, entities, score_threshold, disabled_entities, detection) for t in texts]
        for text, results in zip(texts, analyzed, strict=True):
            for r in results:
                session.register(r.entity_type, text[r.start : r.end])
        masked = []
        for text, results in zip(texts, analyzed, strict=True):
            if not results:
                masked.append(text)
                continue
            masked.append(replace_spans(text, results, session.replacement_for))
        return session.result(masked)
