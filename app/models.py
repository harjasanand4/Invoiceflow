"""Database tables.

Money is stored as integer cents to avoid floating-point rounding errors.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_cents(value: Decimal | float | int | None) -> int | None:
    if value is None:
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal("1")))


def from_cents(value: int | None) -> Decimal | None:
    if value is None:
        return None
    return (Decimal(value) / 100).quantize(Decimal("0.01"))


class DocumentStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"

    ALL = [QUEUED, PROCESSING, NEEDS_REVIEW, AUTO_APPROVED, APPROVED, REJECTED, FAILED]


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    # SHA-256 of the file bytes. Unique, so uploading the same file twice is a no-op.
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(32), default="upload")
    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.QUEUED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractor_used: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    processing_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    invoice: Mapped[Invoice | None] = relationship(
        back_populates="document", uselist=False, cascade="all, delete-orphan"
    )
    issues: Mapped[list[ValidationIssue]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="ValidationIssue.id"
    )
    review_events: Mapped[list[ReviewEvent]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="ReviewEvent.id"
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), unique=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Lower-cased, punctuation-free vendor name used for duplicate detection.
    vendor_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    subtotal_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tax_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_extraction: Mapped[dict] = mapped_column(JSON, default=dict)

    document: Mapped[Document] = relationship(back_populates="invoice")
    line_items: Mapped[list[LineItem]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="LineItem.position"
    )


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(512), default="")
    quantity: Mapped[str | None] = mapped_column(String(32), nullable=True)  # decimal as text
    unit_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16))  # "error" blocks auto-approval, "warning" doesn't
    field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="issues")


class ReviewEvent(Base):
    """Audit trail: every human correction or decision."""

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))  # correct | approve | reject | reprocess
    field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(128), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="review_events")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("po_number", name="uq_po_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(64), index=True)
    vendor_name: Mapped[str] = mapped_column(String(255))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
