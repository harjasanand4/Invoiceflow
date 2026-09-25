from datetime import date
from decimal import Decimal

from app.schemas import InvoiceData, LineItemData
from app.validation import validate_invoice, vendor_key

TODAY = date(2026, 9, 1)


def make(**overrides) -> InvoiceData:
    base = dict(
        vendor_name="Acme Ltd.",
        invoice_number="INV-1",
        invoice_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        currency="CAD",
        subtotal=Decimal("100.00"),
        tax=Decimal("5.00"),
        total=Decimal("105.00"),
        line_items=[
            LineItemData(description="A", quantity=Decimal(2), unit_price=Decimal("25.00"), amount=Decimal("50.00")),
            LineItemData(description="B", quantity=Decimal(1), unit_price=Decimal("50.00"), amount=Decimal("50.00")),
        ],
    )
    base.update(overrides)
    return InvoiceData(**base)


def codes(data, settings):
    return {i.code for i in validate_invoice(data, settings, today=TODAY)}


def test_clean_invoice_has_no_issues(settings):
    assert codes(make(), settings) == set()


def test_total_mismatch(settings):
    assert "TOTAL_MISMATCH" in codes(make(total=Decimal("110.00")), settings)


def test_one_cent_rounding_is_tolerated(settings):
    assert "TOTAL_MISMATCH" not in codes(make(total=Decimal("105.01")), settings)


def test_line_items_sum_mismatch(settings):
    assert "LINE_ITEMS_SUM_MISMATCH" in codes(make(subtotal=Decimal("120.00"), tax=Decimal("6.00"), total=Decimal("126.00")), settings)


def test_line_item_math(settings):
    items = [LineItemData(description="A", quantity=Decimal(3), unit_price=Decimal("10.00"), amount=Decimal("100.00"))]
    assert "LINE_ITEM_MATH" in codes(make(line_items=items), settings)


def test_missing_required_fields(settings):
    issues = validate_invoice(make(invoice_number=None, total=None), settings, today=TODAY)
    missing = {i.field for i in issues if i.code == "MISSING_FIELD"}
    assert missing == {"invoice_number", "total"}


def test_unusual_tax_rate_is_a_warning(settings):
    issues = validate_invoice(make(tax=Decimal("9.00"), total=Decimal("109.00")), settings, today=TODAY)
    unusual = [i for i in issues if i.code == "TAX_RATE_UNUSUAL"]
    assert unusual and unusual[0].severity == "warning"


def test_hst_and_quebec_rates_are_known(settings):
    assert "TAX_RATE_UNUSUAL" not in codes(make(tax=Decimal("13.00"), total=Decimal("113.00")), settings)
    assert "TAX_RATE_UNUSUAL" not in codes(make(tax=Decimal("14.98"), total=Decimal("114.98")), settings)


def test_date_checks(settings):
    assert "DATE_IN_FUTURE" in codes(make(invoice_date=date(2026, 12, 1), due_date=None), settings)
    assert "DUE_BEFORE_ISSUE" in codes(make(due_date=date(2026, 7, 1)), settings)


def test_vendor_key_normalizes_names():
    assert vendor_key("ACME Supplies Ltd.") == vendor_key("Acme Supplies LTD") == "acme supplies"
    assert vendor_key(None) is None
