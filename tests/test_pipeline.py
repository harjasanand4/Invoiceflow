"""Hybrid extraction, retries and idempotency (no real API calls: the LLM is faked)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.db import Session, create_tables, init_engine
from app.extraction import HybridExtractor
from app.extraction.llm import AGREE, DISAGREE, LLMExtractor, parse_json_loose
from app.models import DocumentStatus
from app.processor import ingest_bytes, process_document, process_pending
from app.storage import LocalStorage
from tests.conftest import FakeLLMClient, label_as_llm_output, pdf_for


@pytest.fixture
def session(settings):
    init_engine(settings.database_url)
    create_tables()
    yield Session()
    Session.remove()


def first(labels, template):
    return next(label for label in labels if label["template"] == template and not label["seeded_issues"])


def test_hybrid_skips_llm_when_rules_are_confident(dataset, labels, settings):
    label = first(labels, "classic")
    fake = FakeLLMClient(label_as_llm_output(label))
    result = HybridExtractor(LLMExtractor(fake), settings.review_confidence_threshold).extract(pdf_for(dataset, label))
    assert fake.calls == 0
    assert result.extractor == "hybrid:rules"


def test_hybrid_calls_llm_for_unseen_layout(dataset, labels, settings):
    label = first(labels, "freeform")
    fake = FakeLLMClient(label_as_llm_output(label))
    result = HybridExtractor(LLMExtractor(fake), settings.review_confidence_threshold).extract(pdf_for(dataset, label))
    assert fake.calls == 1
    assert result.extractor == "hybrid:llm"
    assert result.data.invoice_number == label["expected"]["invoice_number"]
    assert result.llm_calls == 1


def test_llm_confidence_comes_from_agreement(dataset, labels, settings):
    label = first(labels, "classic")
    wrong = label_as_llm_output(label)
    wrong["invoice_number"] = "SOMETHING-ELSE"
    from app.extraction.rules import RulesExtractor

    pdf = pdf_for(dataset, label)
    reference = RulesExtractor().extract(pdf)
    result = LLMExtractor(FakeLLMClient(wrong)).extract(pdf, reference=reference)
    assert result.confidence["vendor_name"] == AGREE
    assert result.confidence["invoice_number"] == DISAGREE  # disagreement -> human review


def test_malformed_llm_output_is_retried_then_failed(dataset, labels, settings, session):
    settings = replace(settings, max_attempts=3)
    label = first(labels, "freeform")
    fake = FakeLLMClient({"total": "not-a-number", "line_items": []})
    extractor = HybridExtractor(LLMExtractor(fake), settings.review_confidence_threshold)
    storage = LocalStorage(settings.storage_dir)
    doc, _ = ingest_bytes(session, storage, pdf_for(dataset, label), label["file"])

    doc = process_document(session, storage, doc, extractor, settings)
    assert doc.status == DocumentStatus.QUEUED and doc.attempts == 1 and doc.last_error
    process_pending(session, storage, extractor, settings)
    assert doc.status == DocumentStatus.FAILED
    assert doc.attempts == 3
    assert fake.calls == 3


def test_api_errors_are_retried(dataset, labels, settings, session):
    label = first(labels, "freeform")
    fake = FakeLLMClient(TimeoutError("API timed out"))
    extractor = HybridExtractor(LLMExtractor(fake), settings.review_confidence_threshold)
    storage = LocalStorage(settings.storage_dir)
    doc, _ = ingest_bytes(session, storage, pdf_for(dataset, label), label["file"])
    process_pending(session, storage, extractor, settings)
    assert doc.status == DocumentStatus.FAILED
    assert "TimeoutError" in doc.last_error


def test_ingest_is_idempotent(dataset, labels, settings, session):
    storage = LocalStorage(settings.storage_dir)
    pdf = pdf_for(dataset, labels[0])
    first_doc, created = ingest_bytes(session, storage, pdf, "a.pdf")
    second_doc, created_again = ingest_bytes(session, storage, pdf, "renamed-copy.pdf")
    assert created and not created_again
    assert first_doc.id == second_doc.id


def test_reprocessing_replaces_results(dataset, labels, settings, session):
    from app.extraction.rules import RulesExtractor

    storage = LocalStorage(settings.storage_dir)
    label = first(labels, "classic")
    doc, _ = ingest_bytes(session, storage, pdf_for(dataset, label), label["file"])
    process_document(session, storage, doc, RulesExtractor(), settings)
    n_items, n_issues = len(doc.invoice.line_items), len(doc.issues)
    process_document(session, storage, doc, RulesExtractor(), settings)
    assert len(doc.invoice.line_items) == n_items
    assert len(doc.issues) == n_issues


def test_parse_json_loose_handles_code_fences():
    assert parse_json_loose('Sure!\n```json\n{"total": 5}\n```') == {"total": 5}


def test_weak_regex_guesses_do_not_veto_a_correct_llm_reading(dataset, labels, settings, session):
    """On an unseen layout, a correct LLM reading of a clean invoice can be auto-approved."""
    from app.extraction.rules import RulesExtractor

    label = next(
        lb for lb in labels
        if lb["template"] == "freeform" and not lb["seeded_issues"] and not lb["expected"]["po_number"]
    )
    pdf = pdf_for(dataset, label)
    reference = RulesExtractor().extract(pdf)
    result = LLMExtractor(FakeLLMClient(label_as_llm_output(label))).extract(pdf, reference=reference)
    assert all(score >= settings.review_confidence_threshold for score in result.confidence.values())


@pytest.mark.skipif(not __import__("os").getenv("TEST_DATABASE_URL"), reason="needs Postgres (SKIP LOCKED)")
def test_parallel_workers_never_process_a_document_twice(dataset, labels, settings, session):
    """Four workers drain the same queue at once. Each document must be processed exactly once."""
    import threading

    from app.extraction.rules import RulesExtractor
    from app.models import Document

    storage = LocalStorage(settings.storage_dir)
    for label in labels:
        ingest_bytes(session, storage, pdf_for(dataset, label), label["file"])

    def worker():
        s = Session.session_factory()  # each thread gets its own connection
        try:
            process_pending(s, storage, RulesExtractor(), settings)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    session.expire_all()
    docs = session.query(Document).all()
    assert len(docs) == len(labels)
    assert all(d.attempts == 1 for d in docs), [(d.id, d.attempts) for d in docs if d.attempts != 1]
    assert all(d.status in (DocumentStatus.AUTO_APPROVED, DocumentStatus.NEEDS_REVIEW) for d in docs)
