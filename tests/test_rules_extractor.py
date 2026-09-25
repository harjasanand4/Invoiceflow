from decimal import Decimal

from app.extraction.rules import RulesExtractor
from tests.conftest import pdf_for

KNOWN_LAYOUTS = {"classic", "modern", "compact"}


def test_known_layouts_extract_every_field(dataset, labels):
    extractor = RulesExtractor()
    checked = 0
    for label in labels:
        if label["template"] not in KNOWN_LAYOUTS:
            continue
        result = extractor.extract(pdf_for(dataset, label))
        exp, got = label["expected"], result.data
        assert got.vendor_name.lower() == exp["vendor_name"].lower(), label["file"]
        assert got.invoice_number == exp["invoice_number"], label["file"]
        assert str(got.invoice_date) == exp["invoice_date"], label["file"]
        assert got.total == Decimal(exp["total"]), label["file"]
        assert len(got.line_items) == len(exp["line_items"]), label["file"]
        checked += 1
    assert checked >= 10


def test_consistent_totals_boost_confidence():
    text = """ACME Supplies Ltd.
Invoice #: INV-1
Invoice Date: 2026-05-01
Description Qty Unit Price Amount
Widget 2 10.00 20.00
Subtotal 20.00
GST (5%) 1.00
Total 21.00
All amounts in CAD"""
    result = RulesExtractor().extract_from_text(text)
    assert result.data.total == Decimal("21.00")
    assert result.confidence["total"] >= 0.99
    assert result.confidence["line_items"] >= 0.95


def test_patterns_do_not_cross_line_breaks():
    # Regression test: "INVOICE" followed by a vendor starting with "No..." was read as "Invoice No".
    text = "INVOICE\nNorthern Lights Office Co.\nInvoice #: INV-2026-6912\nTotal 5.00"
    result = RulesExtractor().extract_from_text(text)
    assert result.data.invoice_number == "INV-2026-6912"
    assert result.data.vendor_name == "Northern Lights Office Co."


def test_unseen_layout_is_not_trusted(dataset, labels):
    """On the held-out layout, rules must not be confident enough to auto-approve."""
    extractor = RulesExtractor()
    for label in labels:
        if label["template"] != "freeform":
            continue
        result = extractor.extract(pdf_for(dataset, label))
        low = [f for f, c in result.confidence.items() if c < 0.85]
        missing = [f for f in ("vendor_name", "invoice_number", "invoice_date", "total")
                   if getattr(result.data, f) is None]
        assert low or missing, label["file"]
