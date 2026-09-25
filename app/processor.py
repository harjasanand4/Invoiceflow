"""The pipeline: ingest -> extract -> validate -> save -> route.

Designed to be safe to run repeatedly:
- Ingest is idempotent: the same file (same SHA-256) is only stored once.
- Processing replaces any earlier results for the document instead of appending.
- Failures are retried up to MAX_ATTEMPTS, then the document is marked failed
  with the error saved, so nothing silently disappears.
- Workers claim documents with SELECT ... FOR UPDATE SKIP LOCKED on Postgres, so
  several workers can run at once without processing the same document twice.
"""
from __future__ import annotations

import hashlib
import io
import logging
import time
from datetime import UTC, datetime, timedelta

import pdfplumber
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.extraction import Extractor
from app.extraction.pdf_text import extract_text, is_pdf
from app.models import (
    Document,
    DocumentStatus,
    Invoice,
    LineItem,
    ValidationIssue,
    to_cents,
    utcnow,
)
from app.schemas import ExtractionResult, InvoiceData
from app.storage import Storage
from app.validation import Issue, confidence_issues, database_checks, validate_invoice, vendor_key

log = logging.getLogger("invoiceflow.processor")


class NotAPdfError(ValueError):
    pass


def ingest_bytes(
    session: Session, storage: Storage, data: bytes, filename: str, source: str = "upload"
) -> tuple[Document, bool]:
    """Store a file and create a queued Document. Returns (document, created)."""
    if not is_pdf(data):
        raise NotAPdfError(f"{filename} is not a PDF")
    digest = hashlib.sha256(data).hexdigest()
    existing = session.execute(select(Document).where(Document.sha256 == digest)).scalar_one_or_none()
    if existing:
        log.info("Skipping %s: already ingested as document #%s", filename, existing.id)
        return existing, False

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
    except Exception as exc:  # noqa: BLE001  (corrupt or encrypted file)
        raise NotAPdfError(f"{filename} could not be opened as a PDF: {exc}") from exc

    key = storage.save(f"{digest[:2]}/{digest}.pdf", data)
    doc = Document(
        filename=filename[:255],
        sha256=digest,
        storage_key=key,
        size_bytes=len(data),
        page_count=page_count,
        source=source,
        status=DocumentStatus.QUEUED,
    )
    session.add(doc)
    session.commit()
    log.info("Ingested %s as document #%s", filename, doc.id)
    return doc, True


def save_extraction(
    session: Session, doc: Document, result: ExtractionResult, record_raw: bool = True
) -> Invoice:
    """Write extracted data to the invoice tables, replacing any earlier extraction.

    record_raw=False is used for human corrections, so the original machine output
    is kept for auditing and for measuring how often reviewers had to fix it.
    """
    d = result.data
    invoice = doc.invoice or Invoice(document=doc)
    invoice.vendor_name = d.vendor_name
    invoice.vendor_key = vendor_key(d.vendor_name)
    invoice.invoice_number = d.invoice_number
    invoice.invoice_date = d.invoice_date
    invoice.due_date = d.due_date
    invoice.po_number = d.po_number
    invoice.currency = d.currency
    invoice.subtotal_cents = to_cents(d.subtotal)
    invoice.tax_cents = to_cents(d.tax)
    invoice.total_cents = to_cents(d.total)
    invoice.confidence = result.confidence
    if record_raw:
        invoice.raw_extraction = {"data": d.model_dump(mode="json"), "notes": result.notes}
    invoice.line_items.clear()
    for pos, item in enumerate(d.line_items):
        invoice.line_items.append(
            LineItem(
                position=pos,
                description=item.description,
                quantity=str(item.quantity) if item.quantity is not None else None,
                unit_price_cents=to_cents(item.unit_price),
                amount_cents=to_cents(item.amount),
            )
        )
    session.add(invoice)
    session.flush()
    return invoice


def run_validation(session: Session, doc: Document, data: InvoiceData, confidence: dict, settings: Settings) -> list[Issue]:
    issues = validate_invoice(data, settings)
    issues += database_checks(session, data, doc.id, settings)
    issues += confidence_issues(confidence, settings.review_confidence_threshold)
    doc.issues.clear()
    for issue in issues:
        doc.issues.append(
            ValidationIssue(code=issue.code, severity=issue.severity, field=issue.field, message=issue.message)
        )
    return issues


def invoice_to_data(invoice: Invoice) -> InvoiceData:
    from app.serializers import invoice_json

    j = invoice_json(invoice)
    return InvoiceData.model_validate({k: v for k, v in j.items() if k not in ("confidence", "notes")})


def revalidate_related(session: Session, doc: Document, settings: Settings) -> list[int]:
    """Re-check other documents from the same vendor after a human correction.

    If the original of a re-sent invoice was misread, its duplicate can slip through
    (there was nothing correct to match against). Once a reviewer fixes the original,
    this pulls any auto-approved duplicate back into the review queue.
    Returns the ids of documents that were sent back to review.
    """
    inv = doc.invoice
    if not inv or not inv.vendor_key:
        return []
    others = session.execute(
        select(Document)
        .join(Invoice)
        .where(
            Invoice.vendor_key == inv.vendor_key,
            Document.id != doc.id,
            Document.status.in_([DocumentStatus.AUTO_APPROVED, DocumentStatus.NEEDS_REVIEW]),
        )
    ).scalars().all()
    demoted = []
    for other in others:
        issues = run_validation(
            session, other, invoice_to_data(other.invoice), other.invoice.confidence or {}, settings
        )
        if other.status == DocumentStatus.AUTO_APPROVED and route(issues) == DocumentStatus.NEEDS_REVIEW:
            other.status = DocumentStatus.NEEDS_REVIEW
            demoted.append(other.id)
    return demoted


def route(issues: list[Issue]) -> str:
    """Auto-approve only when nothing blocks it and every field was read confidently."""
    needs_human = any(i.severity == "error" or i.code == "LOW_CONFIDENCE" for i in issues)
    return DocumentStatus.NEEDS_REVIEW if needs_human else DocumentStatus.AUTO_APPROVED


def process_document(
    session: Session, storage: Storage, doc: Document, extractor: Extractor, settings: Settings
) -> Document:
    started = time.perf_counter()
    doc.status = DocumentStatus.PROCESSING
    doc.attempts += 1
    session.commit()
    try:
        pdf_bytes = storage.read(doc.storage_key)
        text = extract_text(pdf_bytes)
        result = extractor.extract(pdf_bytes, text)
        save_extraction(session, doc, result)
        issues = run_validation(session, doc, result.data, result.confidence, settings)
        doc.status = route(issues)
        doc.extractor_used = result.extractor
        doc.llm_calls += result.llm_calls
        doc.last_error = None
        doc.processed_at = utcnow()
        doc.processing_ms = int((time.perf_counter() - started) * 1000)
        session.commit()
        log.info("Document #%s -> %s (%d issues, %s)", doc.id, doc.status, len(issues), result.extractor)
    except Exception as exc:  # noqa: BLE001  (any failure must be recorded, never lost)
        session.rollback()
        doc = session.get(Document, doc.id)
        doc.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        doc.status = DocumentStatus.FAILED if doc.attempts >= settings.max_attempts else DocumentStatus.QUEUED
        session.commit()
        log.warning("Document #%s failed (attempt %d/%d): %s", doc.id, doc.attempts, settings.max_attempts, exc)
    return doc


def claim_next(session: Session) -> Document | None:
    """Claim one queued document. On Postgres, SKIP LOCKED lets several workers run safely."""
    stmt = (
        select(Document)
        .where(Document.status == DocumentStatus.QUEUED)
        .order_by(Document.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return session.execute(stmt).scalar_one_or_none()


def requeue_stuck(session: Session, older_than: timedelta = timedelta(minutes=10)) -> int:
    """Documents left in 'processing' by a crashed worker go back in the queue."""
    cutoff = datetime.now(UTC) - older_than
    stuck = session.execute(
        select(Document).where(Document.status == DocumentStatus.PROCESSING, Document.updated_at < cutoff)
    ).scalars().all()
    for doc in stuck:
        doc.status = DocumentStatus.QUEUED
    session.commit()
    return len(stuck)


def process_pending(
    session: Session, storage: Storage, extractor: Extractor, settings: Settings, limit: int = 100
) -> int:
    processed = 0
    while processed < limit:
        doc = claim_next(session)
        if doc is None:
            break
        process_document(session, storage, doc, extractor, settings)
        processed += 1
    return processed
