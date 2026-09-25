"""REST API used by the review UI (and by anything else that wants to integrate)."""
from __future__ import annotations

import csv
import io
import json

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import ValidationError
from sqlalchemy import func, or_, select

from app.db import Session
from app.models import (
    Document,
    DocumentStatus,
    Invoice,
    PurchaseOrder,
    ReviewEvent,
    ValidationIssue,
    to_cents,
    utcnow,
)
from app.processor import (
    NotAPdfError,
    ingest_bytes,
    process_document,
    revalidate_related,
    run_validation,
    save_extraction,
)
from app.schemas import HEADER_FIELDS, ExtractionResult, InvoiceData
from app.serializers import document_detail, document_summary, invoice_json, money

api = Blueprint("api", __name__, url_prefix="/api")


def _settings():
    return current_app.config["SETTINGS"]


def _storage():
    return current_app.config["STORAGE"]


def _extractor():
    return current_app.config["EXTRACTOR"]


def _reviewer() -> str:
    # Name shown in the audit trail. Real per-user accounts are on the roadmap.
    return (request.headers.get("X-Reviewer") or "reviewer")[:128]


def _error(message: str, status: int, **extra):
    return jsonify({"error": message, **extra}), status


def _get_doc(doc_id: int) -> Document | None:
    return Session().get(Document, doc_id)


@api.get("/health")
def health():
    Session().execute(select(1))
    return {"status": "ok"}


@api.post("/documents")
def upload_documents():
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return _error("Send one or more PDFs in a multipart field named 'files'", 400)
    session = Session()
    results, errors = [], []
    for f in files:
        try:
            doc, created = ingest_bytes(session, _storage(), f.read(), f.filename or "upload.pdf")
        except NotAPdfError as exc:
            errors.append({"filename": f.filename, "error": str(exc)})
            continue
        if created and _settings().process_on_upload:
            doc = process_document(session, _storage(), doc, _extractor(), _settings())
        results.append({**document_summary(doc), "duplicate_upload": not created})
    status = 201 if any(not r["duplicate_upload"] for r in results) else 200
    if errors and not results:
        status = 400
    return jsonify({"documents": results, "errors": errors}), status


@api.get("/documents")
def list_documents():
    session = Session()
    stmt = select(Document).outerjoin(Invoice).order_by(Document.id.desc())
    status = request.args.get("status")
    if status:
        stmt = stmt.where(Document.status.in_(status.split(",")))
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Invoice.vendor_name.ilike(like), Invoice.invoice_number.ilike(like), Document.filename.ilike(like))
        )
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 50)), 1), 200)
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    docs = session.execute(stmt.limit(per_page).offset((page - 1) * per_page)).scalars().all()
    return {"documents": [document_summary(d) for d in docs], "total": total, "page": page, "per_page": per_page}


@api.get("/documents/<int:doc_id>")
def get_document(doc_id: int):
    doc = _get_doc(doc_id)
    if not doc:
        return _error("Document not found", 404)
    return document_detail(doc)


@api.get("/documents/<int:doc_id>/file")
def get_file(doc_id: int):
    doc = _get_doc(doc_id)
    if not doc:
        return _error("Document not found", 404)
    data = _storage().read(doc.storage_key)
    return Response(
        data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


@api.get("/documents/<int:doc_id>/pages/<int:page>.png")
def page_image(doc_id: int, page: int):
    """Render one page as a PNG. Works in every browser, including mobile, unlike inline PDFs."""
    import pdfplumber

    doc = _get_doc(doc_id)
    if not doc:
        return _error("Document not found", 404)
    with pdfplumber.open(io.BytesIO(_storage().read(doc.storage_key))) as pdf:
        if not 1 <= page <= len(pdf.pages):
            return _error("No such page", 404)
        image = pdf.pages[page - 1].to_image(resolution=120).original
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        headers = {"X-Page-Count": str(len(pdf.pages)), "Cache-Control": "private, max-age=3600"}
    return Response(buf.getvalue(), mimetype="image/png", headers=headers)


@api.put("/documents/<int:doc_id>/invoice")
def correct_invoice(doc_id: int):
    """Save a reviewer's corrections. Every changed field is recorded in the audit trail."""
    session = Session()
    doc = _get_doc(doc_id)
    if not doc or not doc.invoice:
        return _error("Document has no extracted invoice to correct", 404)
    if doc.status in (DocumentStatus.APPROVED, DocumentStatus.REJECTED):
        return _error(f"Document is already {doc.status}", 409)

    current = invoice_json(doc.invoice)
    changes = request.get_json(silent=True) or {}
    merged = {k: current[k] for k in HEADER_FIELDS + ["line_items"]}
    merged.update({k: v for k, v in changes.items() if k in merged})
    try:
        data = InvoiceData.model_validate(merged)
    except ValidationError as exc:
        return _error("Invalid values", 422, details=json.loads(exc.json()))

    normalized = data.model_dump(mode="json")
    before = InvoiceData.model_validate({k: current[k] for k in merged}).model_dump(mode="json")
    confidence = dict(doc.invoice.confidence or {})
    for field in HEADER_FIELDS + ["line_items"]:
        if normalized[field] != before[field]:
            session.add(ReviewEvent(
                document=doc,
                action="correct",
                field=field,
                old_value=json.dumps(before[field]),
                new_value=json.dumps(normalized[field]),
                reviewer=_reviewer(),
            ))
            confidence[field] = 1.0  # a human confirmed it
    result = ExtractionResult(data=data, confidence=confidence, extractor=doc.extractor_used or "manual")
    save_extraction(session, doc, result, record_raw=False)
    run_validation(session, doc, data, confidence, _settings())
    doc.status = DocumentStatus.NEEDS_REVIEW
    session.flush()
    reopened = revalidate_related(session, doc, _settings())
    session.commit()
    return {**document_detail(doc), "reopened_documents": reopened}


@api.post("/documents/<int:doc_id>/approve")
def approve(doc_id: int):
    session = Session()
    doc = _get_doc(doc_id)
    if not doc:
        return _error("Document not found", 404)
    if doc.status not in (DocumentStatus.NEEDS_REVIEW, DocumentStatus.AUTO_APPROVED):
        return _error(f"Can't approve a document that is {doc.status}", 409)
    body = request.get_json(silent=True) or {}
    blocking = [i for i in doc.issues if i.severity == "error"]
    if blocking and not body.get("override"):
        return _error(
            "Document has unresolved errors. Fix them, or approve with override and a note.",
            409,
            issues=[{"code": i.code, "message": i.message} for i in blocking],
        )
    if blocking and not (body.get("note") or "").strip():
        return _error("Overriding errors requires a note explaining why", 400)
    doc.status = DocumentStatus.APPROVED
    doc.reviewed_at = utcnow()
    session.add(ReviewEvent(document=doc, action="approve", note=body.get("note"), reviewer=_reviewer()))
    session.commit()
    return document_detail(doc)


@api.post("/documents/<int:doc_id>/reject")
def reject(doc_id: int):
    session = Session()
    doc = _get_doc(doc_id)
    if not doc:
        return _error("Document not found", 404)
    body = request.get_json(silent=True) or {}
    note = (body.get("note") or "").strip()
    if not note:
        return _error("Rejecting requires a note", 400)
    doc.status = DocumentStatus.REJECTED
    doc.reviewed_at = utcnow()
    session.add(ReviewEvent(document=doc, action="reject", note=note, reviewer=_reviewer()))
    session.commit()
    return document_detail(doc)


@api.post("/documents/<int:doc_id>/reprocess")
def reprocess(doc_id: int):
    session = Session()
    doc = _get_doc(doc_id)
    if not doc:
        return _error("Document not found", 404)
    if doc.status == DocumentStatus.APPROVED:
        return _error("Approved documents can't be reprocessed", 409)
    doc.status = DocumentStatus.QUEUED
    doc.attempts = 0
    session.add(ReviewEvent(document=doc, action="reprocess", reviewer=_reviewer()))
    session.commit()
    if _settings().process_on_upload:
        doc = process_document(session, _storage(), doc, _extractor(), _settings())
    return document_detail(doc)


@api.get("/stats")
def stats():
    session = Session()
    by_status = dict(session.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    finished = sum(by_status.get(s, 0) for s in (
        DocumentStatus.NEEDS_REVIEW, DocumentStatus.AUTO_APPROVED, DocumentStatus.APPROVED, DocumentStatus.REJECTED
    ))
    # "Touchless" = approved by the system with no human edits.
    touchless = by_status.get(DocumentStatus.AUTO_APPROVED, 0)
    avg_ms = session.execute(select(func.avg(Document.processing_ms))).scalar()
    llm_calls = session.execute(select(func.coalesce(func.sum(Document.llm_calls), 0))).scalar()
    top_issues = session.execute(
        select(ValidationIssue.code, ValidationIssue.severity, func.count())
        .group_by(ValidationIssue.code, ValidationIssue.severity)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    corrections = session.execute(
        select(ReviewEvent.field, func.count())
        .where(ReviewEvent.action == "correct")
        .group_by(ReviewEvent.field)
        .order_by(func.count().desc())
    ).all()
    total_value = session.execute(
        select(func.coalesce(func.sum(Invoice.total_cents), 0))
        .join(Document)
        .where(Document.status.in_([DocumentStatus.APPROVED, DocumentStatus.AUTO_APPROVED]))
    ).scalar()
    return {
        "by_status": {s: by_status.get(s, 0) for s in DocumentStatus.ALL},
        "total_documents": sum(by_status.values()),
        "touchless_rate": round(touchless / finished, 4) if finished else None,
        "avg_processing_ms": round(avg_ms) if avg_ms is not None else None,
        "llm_calls": int(llm_calls),
        "approved_value": money(total_value),
        "top_issues": [{"code": c, "severity": s, "count": n} for c, s, n in top_issues],
        "corrections_by_field": [{"field": f, "count": n} for f, n in corrections],
    }


@api.get("/export.csv")
def export_csv():
    """Approved invoices in a flat CSV, ready for import into an accounting system."""
    session = Session()
    rows = session.execute(
        select(Document, Invoice)
        .join(Invoice)
        .where(Document.status.in_([DocumentStatus.APPROVED, DocumentStatus.AUTO_APPROVED]))
        .order_by(Invoice.invoice_date)
    ).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["document_id", "vendor", "invoice_number", "invoice_date", "due_date",
                     "po_number", "currency", "subtotal", "tax", "total", "status"])
    for doc, inv in rows:
        writer.writerow([doc.id, inv.vendor_name, inv.invoice_number, inv.invoice_date, inv.due_date,
                         inv.po_number, inv.currency, money(inv.subtotal_cents), money(inv.tax_cents),
                         money(inv.total_cents), doc.status])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=approved_invoices.csv"})


@api.get("/purchase-orders")
def list_pos():
    pos = Session().execute(select(PurchaseOrder).order_by(PurchaseOrder.po_number)).scalars().all()
    return {"purchase_orders": [
        {"po_number": p.po_number, "vendor_name": p.vendor_name, "amount": money(p.amount_cents),
         "currency": p.currency, "status": p.status}
        for p in pos
    ]}


@api.post("/purchase-orders")
def create_pos():
    """Create or update purchase orders. Accepts one object or a list."""
    session = Session()
    body = request.get_json(silent=True)
    items = body if isinstance(body, list) else [body] if body else []
    if not items:
        return _error("Send a PO object or a list of them", 400)
    saved = 0
    for item in items:
        try:
            number, vendor, amount = item["po_number"], item["vendor_name"], item["amount"]
        except (KeyError, TypeError):
            return _error("Each PO needs po_number, vendor_name and amount", 400)
        po = session.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == number)).scalar_one_or_none()
        po = po or PurchaseOrder(po_number=number)
        po.vendor_name = vendor
        po.amount_cents = to_cents(amount)
        po.currency = item.get("currency", "CAD")
        po.status = item.get("status", "open")
        session.add(po)
        saved += 1
    session.commit()
    return {"saved": saved}, 201

