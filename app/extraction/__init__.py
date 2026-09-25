"""Extractors turn a PDF into InvoiceData plus per-field confidence.

- rules:  regex only. Free and offline.
- llm:    an LLM reads every document, cross-checked against the rules result.
- hybrid: rules first; the LLM is only called when the rules result isn't confident.
          This is the production default: most invoices from known vendors never
          need a paid API call.
"""
from __future__ import annotations

from typing import Protocol

from app.config import Settings
from app.extraction.llm import LLMExtractor, build_llm_client
from app.extraction.pdf_text import extract_text
from app.extraction.rules import RulesExtractor
from app.schemas import REQUIRED_FIELDS, ExtractionResult


class Extractor(Protocol):
    name: str

    def extract(self, pdf_bytes: bytes, text: str | None = None) -> ExtractionResult: ...


class ModeLLM:
    """LLM on every document, with the rule extractor as an independent cross-check."""

    name = "llm"

    def __init__(self, llm: LLMExtractor):
        self.rules = RulesExtractor()
        self.llm = llm

    def extract(self, pdf_bytes: bytes, text: str | None = None) -> ExtractionResult:
        text = extract_text(pdf_bytes) if text is None else text
        reference = self.rules.extract_from_text(text)
        return self.llm.extract(pdf_bytes, text, reference=reference)


class HybridExtractor:
    name = "hybrid"

    def __init__(self, llm: LLMExtractor, threshold: float):
        self.rules = RulesExtractor()
        self.llm = llm
        self.threshold = threshold

    def rules_are_enough(self, result: ExtractionResult) -> bool:
        data, conf = result.data, result.confidence
        if any(getattr(data, f) is None for f in REQUIRED_FIELDS):
            return False
        if any(score < self.threshold for score in conf.values()):
            return False
        if not data.line_items:
            return False
        return True

    def extract(self, pdf_bytes: bytes, text: str | None = None) -> ExtractionResult:
        text = extract_text(pdf_bytes) if text is None else text
        rules_result = self.rules.extract_from_text(text)
        if self.rules_are_enough(rules_result):
            rules_result.extractor = "hybrid:rules"
            return rules_result
        result = self.llm.extract(pdf_bytes, text, reference=rules_result)
        result.extractor = "hybrid:llm"
        return result


def build_extractor(settings: Settings, llm_client=None) -> Extractor:
    mode = settings.extractor
    if mode == "rules":
        return RulesExtractor()
    llm = LLMExtractor(llm_client or build_llm_client(settings), settings.review_confidence_threshold)
    if mode == "llm":
        return ModeLLM(llm)
    if mode == "hybrid":
        return HybridExtractor(llm, settings.review_confidence_threshold)
    raise ValueError(f"Unknown EXTRACTOR: {mode}")
