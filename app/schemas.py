"""The shape of extracted invoice data, shared by every extractor.

Pydantic validates LLM output, so a malformed response fails loudly instead of
silently writing bad data to the database.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

# Fields that are compared in evals and scored for confidence.
HEADER_FIELDS = [
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "currency",
    "subtotal",
    "tax",
    "total",
]
REQUIRED_FIELDS = ["vendor_name", "invoice_number", "invoice_date", "total"]
MONEY_FIELDS = ["subtotal", "tax", "total"]


class LineItemData(BaseModel):
    description: str = ""
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class InvoiceData(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    po_number: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    line_items: list[LineItemData] = Field(default_factory=list)

    @field_validator("vendor_name", "invoice_number", "po_number", "currency", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v):
        return v.upper() if v else v


class ExtractionResult(BaseModel):
    data: InvoiceData
    # Per-field confidence between 0 and 1.
    confidence: dict[str, float] = Field(default_factory=dict)
    extractor: str
    llm_calls: int = 0
    notes: list[str] = Field(default_factory=list)
