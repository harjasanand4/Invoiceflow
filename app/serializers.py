"""Convert database rows to JSON-friendly dicts for the API."""
from __future__ import annotations

from app.models import Document, Invoice, LineItem, from_cents


def money(cents: int | None) -> str | None:
    value = from_cents(cents)
    return f"{value:.2f}" if value is not None else None


def line_item_json(li: LineItem) -> dict:
    return {
        "description": li.description,
        "quantity": li.quantity,
        "unit_price": money(li.unit_price_cents),
        "amount": money(li.amount_cents),
    }


def invoice_json(inv: Invoice | None) -> dict | None:
    if inv is None:
        return None
    return {
        "vendor_name": inv.vendor_name,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "po_number": inv.po_number,
        "currency": inv.currency,
        "subtotal": money(inv.subtotal_cents),
        "tax": money(inv.tax_cents),
        "total": money(inv.total_cents),
        "confidence": inv.confidence or {},
        "line_items": [line_item_json(li) for li in inv.line_items],
        "notes": (inv.raw_extraction or {}).get("notes", []),
    }


def document_summary(doc: Document) -> dict:
    inv = doc.invoice
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "source": doc.source,
        "vendor_name": inv.vendor_name if inv else None,
        "invoice_number": inv.invoice_number if inv else None,
        "total": money(inv.total_cents) if inv else None,
        "currency": inv.currency if inv else None,
        "error_count": sum(1 for i in doc.issues if i.severity == "error"),
        "warning_count": sum(1 for i in doc.issues if i.severity == "warning"),
        "extractor_used": doc.extractor_used,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def document_detail(doc: Document) -> dict:
    return {
        **document_summary(doc),
        "sha256": doc.sha256,
        "page_count": doc.page_count,
        "size_bytes": doc.size_bytes,
        "attempts": doc.attempts,
        "last_error": doc.last_error,
        "llm_calls": doc.llm_calls,
        "processing_ms": doc.processing_ms,
        "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
        "reviewed_at": doc.reviewed_at.isoformat() if doc.reviewed_at else None,
        "invoice": invoice_json(doc.invoice),
        "issues": [
            {"code": i.code, "severity": i.severity, "field": i.field, "message": i.message}
            for i in doc.issues
        ],
        "history": [
            {
                "action": e.action,
                "field": e.field,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "note": e.note,
                "reviewer": e.reviewer,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in doc.review_events
        ],
    }
