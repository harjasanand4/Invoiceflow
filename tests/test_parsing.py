from datetime import date
from decimal import Decimal

import pytest

from app.extraction.parsing import parse_date, parse_money


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-08-14", date(2026, 8, 14)),
        ("Aug 14, 2026", date(2026, 8, 14)),
        ("August 14, 2026", date(2026, 8, 14)),
        ("14 Aug 2026", date(2026, 8, 14)),
        ("14/08/2026", date(2026, 8, 14)),  # day > 12, so unambiguous
        ("08/14/2026", date(2026, 8, 14)),  # month-first, also unambiguous
    ],
)
def test_parse_date_formats(text, expected):
    parsed, confidence = parse_date(text)
    assert parsed == expected
    assert confidence >= 0.9


def test_ambiguous_slash_date_is_low_confidence():
    parsed, confidence = parse_date("04/08/2026")
    assert parsed == date(2026, 8, 4)  # day-first, the Canadian default
    assert confidence < 0.85  # low enough to force human review


def test_invalid_date():
    assert parse_date("31/02/2026") == (None, 0.0)
    assert parse_date("not a date") == (None, 0.0)


@pytest.mark.parametrize(
    "text, expected",
    [("$1,234.50", Decimal("1234.50")), ("99.99", Decimal("99.99")), ("$ 12,000.00", Decimal("12000.00"))],
)
def test_parse_money(text, expected):
    assert parse_money(text) == expected


def test_parse_money_garbage():
    assert parse_money("abc") is None
    assert parse_money(None) is None
