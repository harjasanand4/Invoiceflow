"""Business-rule validation.

Two kinds of checks:
- validate_invoice(): pure checks on the extracted data (math, required fields, dates).
- database_checks(): checks that need history (duplicates, purchase-order matching).

"error" issues block auto-approval. "warning" issues are shown to reviewers but
don't block on their own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Document, DocumentStatus, Invoice, PurchaseOrder, from_cents, to_cents
from app.schemas import REQUIRED_FIELDS, InvoiceData

# Combined sales-tax rates used in Canada (GST, HST, GST+PST, GST+QST), plus 0 for exempt.
KNOWN_TAX_RATES = [
    Decimal("0"), Decimal("0.05"), Decimal("0.11"), Decimal("0.12"),
    Decimal("0.13"), Decimal("0.14975"), Decimal("0.15"),
]


@dataclass
class Issue:
    code: str
    severity: str  # "error" | "warning"
    message: str
    field: str | None = None


def vendor_key(name: str | None) -> str | None:
    """Normalize a vendor name so 'ACME Ltd.' and 'Acme Ltd' compare equal."""
    if not name:
        return None
    key = re.sub(r"[^a-z0-9 ]", "", name.lower())
    key = re.sub(r"\b(ltd|inc|llc|llp|co|corp|corporation|limited|company)\b", "", key)
    return re.sub(r"\s+", " ", key).strip() or None


def validate_invoice(data: InvoiceData, settings: Settings, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    tol = Decimal(str(settings.money_tolerance))
    issues: list[Issue] = []

    for field in REQUIRED_FIELDS:
        if getattr(data, field) in (None, ""):
            issues.append(Issue("MISSING_FIELD", "error", f"Required field '{field}' was not found", field))

    if not data.line_items:
        issues.append(Issue("NO_LINE_ITEMS", "warning", "No line items were extracted", "line_items"))

    for i, item in enumerate(data.line_items, start=1):
        if item.quantity is not None and item.unit_price is not None and item.amount is not None:
            expected = (item.quantity * item.unit_price).quantize(Decimal("0.01"))
            if abs(expected - item.amount) > tol:
                issues.append(Issue(
                    "LINE_ITEM_MATH", "warning",
                    f"Line {i}: {item.quantity} x {item.unit_price} = {expected}, but the invoice says {item.amount}",
                    "line_items",
                ))

    amounts = [li.amount for li in data.line_items if li.amount is not None]
    if data.subtotal is not None and amounts and len(amounts) == len(data.line_items):
        line_sum = sum(amounts)
        if abs(line_sum - data.subtotal) > tol:
            issues.append(Issue(
                "LINE_ITEMS_SUM_MISMATCH", "error",
                f"Line items add up to {line_sum}, but the subtotal is {data.subtotal}",
                "subtotal",
            ))

    if data.subtotal is not None and data.total is not None:
        tax = data.tax or Decimal("0")
        if abs(data.subtotal + tax - data.total) > tol:
            issues.append(Issue(
                "TOTAL_MISMATCH", "error",
                f"Subtotal {data.subtotal} + tax {tax} = {data.subtotal + tax}, but the total is {data.total}",
                "total",
            ))

    if data.subtotal and data.tax is not None and data.subtotal > 0:
        rate = data.tax / data.subtotal
        if not any(abs(rate - known) <= Decimal("0.002") for known in KNOWN_TAX_RATES):
            issues.append(Issue(
                "TAX_RATE_UNUSUAL", "warning",
                f"Tax is {rate:.2%} of the subtotal, which doesn't match a standard Canadian rate",
                "tax",
            ))

    if data.invoice_date:
        if data.invoice_date > today + timedelta(days=1):
            issues.append(Issue("DATE_IN_FUTURE", "warning", f"Invoice date {data.invoice_date} is in the future", "invoice_date"))
        if data.invoice_date < today - timedelta(days=365 * 2):
            issues.append(Issue("DATE_TOO_OLD", "warning", f"Invoice date {data.invoice_date} is over two years old", "invoice_date"))
    if data.invoice_date and data.due_date and data.due_date < data.invoice_date:
        issues.append(Issue("DUE_BEFORE_ISSUE", "warning", "Due date is before the invoice date", "due_date"))

    return issues


def database_checks(session: Session, data: InvoiceData, document_id: int, settings: Settings) -> list[Issue]:
    issues: list[Issue] = []
    key = vendor_key(data.vendor_name)

    # Duplicates: other documents (not rejected) with the same vendor and invoice number.
    # A re-sent invoice is a different file, so the file hash alone can't catch it.
    if key and data.invoice_number:
        dup = session.execute(
            select(Document.id)
            .join(Invoice)
            .where(
                Invoice.vendor_key == key,
                Invoice.invoice_number == data.invoice_number,
                Document.id != document_id,
                Document.status != DocumentStatus.REJECTED,
            )
            .limit(1)
        ).scalar_one_or_none()
        if dup:
            issues.append(Issue(
                "DUPLICATE_INVOICE", "error",
                f"Invoice {data.invoice_number} from this vendor was already received (document #{dup})",
                "invoice_number",
            ))
        elif data.total is not None and data.invoice_date:
            # Same vendor, same amount, same day, different number: maybe a re-issued invoice.
            near = session.execute(
                select(Document.id)
                .join(Invoice)
                .where(
                    Invoice.vendor_key == key,
                    Invoice.total_cents == to_cents(data.total),
                    Invoice.invoice_date == data.invoice_date,
                    Document.id != document_id,
                    Document.status != DocumentStatus.REJECTED,
                )
                .limit(1)
            ).scalar_one_or_none()
            if near:
                issues.append(Issue(
                    "POSSIBLE_DUPLICATE", "warning",
                    f"Same vendor, date and total as document #{near}, with a different invoice number",
                    "invoice_number",
                ))

    # Purchase-order matching.
    if data.po_number:
        po = session.execute(
            select(PurchaseOrder).where(PurchaseOrder.po_number == data.po_number)
        ).scalar_one_or_none()
        if po is None:
            issues.append(Issue("PO_NOT_FOUND", "error", f"PO {data.po_number} does not exist", "po_number"))
        else:
            if key and vendor_key(po.vendor_name) != key:
                issues.append(Issue(
                    "PO_VENDOR_MISMATCH", "error",
                    f"PO {po.po_number} was issued to {po.vendor_name}, not {data.vendor_name}",
                    "po_number",
                ))
            if po.status != "open":
                issues.append(Issue("PO_CLOSED", "error", f"PO {po.po_number} is {po.status}", "po_number"))
            if data.total is not None:
                # A PO is often billed across several invoices, so compare the running total.
                # Copies of this same invoice are excluded; they're flagged as duplicates instead.
                billed_cents = session.execute(
                    select(func.coalesce(func.sum(Invoice.total_cents), 0))
                    .join(Document)
                    .where(
                        Invoice.po_number == po.po_number,
                        Document.id != document_id,
                        Document.status != DocumentStatus.REJECTED,
                        ~((Invoice.vendor_key == key) & (Invoice.invoice_number == data.invoice_number)),
                    )
                ).scalar_one()
                already = from_cents(billed_cents)
                po_amount = from_cents(po.amount_cents)
                limit = po_amount * (1 + Decimal(str(settings.po_tolerance)))
                if already + data.total > limit:
                    detail = f" (already billed {already} on earlier invoices)" if already else ""
                    issues.append(Issue(
                        "PO_AMOUNT_EXCEEDED", "error",
                        f"Invoice total {data.total} exceeds the remaining amount on PO {po.po_number} "
                        f"({po_amount}){detail}",
                        "total",
                    ))
            if data.currency and po.currency and data.currency != po.currency:
                issues.append(Issue(
                    "PO_CURRENCY_MISMATCH", "error",
                    f"Invoice is in {data.currency} but PO {po.po_number} is in {po.currency}",
                    "currency",
                ))
    return issues


def confidence_issues(confidence: dict[str, float], threshold: float) -> list[Issue]:
    return [
        Issue("LOW_CONFIDENCE", "warning", f"Low confidence ({score:.2f}) reading '{field}'", field)
        for field, score in sorted(confidence.items())
        if score < threshold
    ]
