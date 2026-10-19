"""Presidio analyzer + anonymizer wiring.

Detection: Presidio ``AnalyzerEngine`` with a spaCy NLP engine per language
plus custom recognizers. Replacement: Presidio ``AnonymizerEngine`` with a
``custom`` operator whose lambda delegates to ``privacy_hub``'s
``MaskingSession`` (placeholder strategy + Hungarian suffix handling).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from functools import partial

import tldextract
from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import PhoneRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from privacy_hub.masking import MaskingSession, MaskResult, Span, get_morphology, resolve_overlaps

log = logging.getLogger("privacy_analyzer.engine")

# Presidio's EmailRecognizer calls tldextract.extract(), which downloads the
# public suffix list on first use. The analyzer runs without network egress,
# so use tldextract's bundled snapshot instead (no network, no cache dir).
tldextract.extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

# Entity types that are detected but not masked by default (too noisy / not
# personal data on their own). Organisations can change this later.
DEFAULT_IGNORED = {"DATE_TIME", "NRP", "URL"}


@dataclass
class EngineInfo:
    languages: list[str]
    nlp_models: dict[str, str]
    load_seconds: float


class PrivacyEngine:
    def __init__(self, languages: list[str] | None = None, en_model: str | None = None) -> None:
        started = time.perf_counter()
        self.languages = languages or os.environ.get("ANALYZER_LANGUAGES", "en").split(",")
        self.nlp_models: dict[str, str] = {}
        models = []
        if "en" in self.languages:
            self.nlp_models["en"] = en_model or os.environ.get("ANALYZER_EN_MODEL", "en_core_web_md")
            models.append({"lang_code": "en", "model_name": self.nlp_models["en"]})
        provider = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy", "models": models})
        nlp_engine = provider.create_engine()
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=self.languages)
        # Presidio's default phone recognizer does not include Hungary.
        self.analyzer.registry.remove_recognizer("PhoneRecognizer")
        self.analyzer.registry.add_recognizer(
            PhoneRecognizer(supported_regions=("HU", "US", "UK", "DE", "AT", "FR"), supported_language="en")
        )
        self.anonymizer = AnonymizerEngine()
        self.info = EngineInfo(self.languages, dict(self.nlp_models), time.perf_counter() - started)
        log.info("engine ready: %s", self.info)

    def analyze(
        self, text: str, language: str, entities: list[str] | None = None, score_threshold: float = 0.4
    ) -> list[RecognizerResult]:
        results = self.analyzer.analyze(text=text, language=language, entities=entities, score_threshold=score_threshold)
        if entities is None:
            results = [r for r in results if r.entity_type not in DEFAULT_IGNORED]
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
    ) -> MaskResult:
        if language not in self.languages:
            raise ValueError(f"language {language!r} is not loaded (available: {self.languages})")
        session = MaskingSession(strategy, get_morphology(language), existing_texts=texts, language=language)
        analyzed = [self.analyze(t, language, entities, score_threshold) for t in texts]
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
