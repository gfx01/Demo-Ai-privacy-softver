"""Presidio analyzer + anonymizer wiring.

Detection: one Presidio ``AnalyzerEngine`` per language, each with its own
pluggable NLP/NER backend, plus custom recognizers (Hungarian identifiers,
gazetteer names). Replacement: Presidio ``AnonymizerEngine`` with a ``custom``
operator whose lambda delegates to ``privacy_hub``'s ``MaskingSession``
(placeholder strategy + Hungarian suffix handling).

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
import tempfile
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import tldextract
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NerModelConfiguration, NlpEngineProvider
from presidio_analyzer.predefined_recognizers import PhoneRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from privacy_hub.masking import MaskingSession, MaskResult, Span, get_morphology, resolve_overlaps
from privacy_hub.masking.pseudonyms import PseudonymStrategy

from .recognizers import HuNameRecognizer, hungarian_recognizers

log = logging.getLogger("privacy_analyzer.engine")

# Presidio's EmailRecognizer calls tldextract.extract(), which downloads the
# public suffix list on first use. The analyzer runs without network egress,
# so use tldextract's bundled snapshot instead (no network, no cache dir).
tldextract.extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

# Entity types that are detected but not masked by default (too noisy / not
# personal data on their own). Organisations can change this later.
DEFAULT_IGNORED = {"DATE_TIME", "NRP", "URL", "IN_PAN", "US_DRIVER_LICENSE", "US_BANK_NUMBER"}

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
                registry.add_recognizer(PhoneRecognizer(supported_regions=("HU", "US", "UK", "DE", "AT", "FR"), supported_language=lang))
            self.analyzers[lang] = AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=[lang])
        self.anonymizer = AnonymizerEngine()
        names = next((r for a in self.analyzers.values() for r in a.registry.recognizers if isinstance(r, HuNameRecognizer)), None)
        self._lexicon = (names.given | names.surnames) if names else set()
        self._male = names.male if names else set()
        self._female = names.female if names else set()
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
    ) -> list[RecognizerResult]:
        analyzer = self.analyzers[language]
        results = analyzer.analyze(text=text, language=language, entities=entities, score_threshold=score_threshold)
        ignored = set(disabled_entities) | (DEFAULT_IGNORED if entities is None else set())
        results = [r for r in results if r.entity_type not in ignored]
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
    ) -> MaskResult:
        if language not in self.analyzers:
            raise ValueError(f"language {language!r} is not loaded (available: {self.languages})")
        strat = PseudonymStrategy(gender_of=self.gender_of) if strategy == "pseudonym" else strategy
        session = MaskingSession(strat, get_morphology(language, self._lexicon), existing_texts=texts, language=language)
        analyzed = [self.analyze(t, language, entities, score_threshold, disabled_entities) for t in texts]
        for text, results in zip(texts, analyzed, strict=True):
            for r in results:
                session.register(r.entity_type, text[r.start : r.end])
        masked = []
        for text, results in zip(texts, analyzed, strict=True):
            if not results:
                masked.append(text)
                continue
            operators = {
                etype: OperatorConfig("custom", {"lambda": partial(session.replacement_for, etype)})
                for etype in {r.entity_type for r in results}
            }
            masked.append(self.anonymizer.anonymize(text=text, analyzer_results=results, operators=operators).text)
        return session.result(masked)
