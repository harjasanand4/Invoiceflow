"""Helpers for parsing messy dates and money amounts."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MONEY_RE = r"\$?\s?-?\d{1,3}(?:,\d{3})*(?:\.\d{2})|\$?\s?-?\d+\.\d{2}"

DATE_PATTERNS = [
    # (regex, strptime format(s))
    (r"\d{4}-\d{2}-\d{2}", ["%Y-%m-%d"]),
    (r"[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}", ["%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y", "%b. %d, %Y"]),
    (r"\d{1,2} [A-Z][a-z]{2,8},? \d{4}", ["%d %b %Y", "%d %B %Y", "%d %b, %Y", "%d %B, %Y"]),
    (r"\d{1,2}/\d{1,2}/\d{4}", ["slash"]),
]
DATE_RE = "|".join(f"(?:{p})" for p, _ in DATE_PATTERNS)


def parse_money(text: str | None) -> Decimal | None:
    if not text:
        return None
    cleaned = text.replace("$", "").replace(",", "").replace(" ", "")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_date(text: str) -> tuple[date | None, float]:
    """Parse a date string and return (date, confidence).

    Slash dates are ambiguous: 04/08/2026 could be April 8 or August 4. If one part
    is over 12 the order is certain; otherwise assume day-first (the Canadian
    default) with low confidence, which sends the invoice to human review.
    """
    text = text.strip().rstrip(".")
    for pattern, formats in DATE_PATTERNS:
        if not re.fullmatch(pattern, text):
            continue
        if formats == ["slash"]:
            a, b, year = (int(x) for x in text.split("/"))
            try:
                if a > 12:
                    return date(year, b, a), 0.95
                if b > 12:
                    return date(year, a, b), 0.95
                if a == b:
                    return date(year, a, b), 0.95
                return date(year, b, a), 0.6
            except ValueError:
                return None, 0.0
        for fmt in formats:
            try:
                return datetime.strptime(text.replace(".", ""), fmt.replace(".", "")).date(), 0.95
            except ValueError:
                continue
    return None, 0.0
