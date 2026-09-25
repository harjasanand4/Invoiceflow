"""Rule-based (regex) extractor.

Free, fast, offline and deterministic. It works well on layouts it has seen and
poorly on ones it hasn't, which makes it a good baseline to compare the LLM
extractor against, and a cheap first pass in hybrid mode.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.extraction.parsing import DATE_RE, MONEY_RE, parse_date, parse_money
from app.extraction.pdf_text import extract_text
from app.schemas import ExtractionResult, InvoiceData, LineItemData

STRONG = 0.95
WEAK = 0.75

DOC_TITLE_RE = re.compile(r"^(tax\s+)?(invoice|receipt|bill|statement)$", re.I)
COMPANY_SUFFIX_RE = re.compile(
    r"\b(ltd|inc|llc|llp|co|corp|corporation|limited|company|gmbh|plc)\.?$", re.I
)

INVOICE_NUMBER_PATTERNS = [
    (re.compile(r"\binvoice\s*(?:#|no\b\.?|number|num\b\.?)\s*:?\s*([A-Z0-9][A-Z0-9\-/]*)", re.I), STRONG),
    (re.compile(r"\binv(?:oice)?\s*#\s*:?\s*([A-Z0-9][A-Z0-9\-/]*)", re.I), STRONG),
    (re.compile(r"\b(?:ref|reference)\b\s*(?:#|no\b\.?)?\s*:?\s*([A-Z0-9][A-Z0-9\-/]*)", re.I), WEAK),
]
INVOICE_DATE_PATTERNS = [
    re.compile(rf"\binvoice\s+date\s*:?\s*({DATE_RE})", re.I),
    re.compile(rf"\b(?:issued|issue date|date issued|dated)\s*:?\s*({DATE_RE})", re.I),
    re.compile(rf"(?<!due )(?<!due)\bdate\s*:?\s*({DATE_RE})", re.I),
]
DUE_DATE_PATTERNS = [
    re.compile(rf"\b(?:due\s+date|payment\s+due|date\s+due|due)\s*:?\s*({DATE_RE})", re.I),
]
PO_PATTERNS = [
    re.compile(
        r"\b(?:po\s*(?:number|no\b\.?|#)|purchase\s+order(?:\s*(?:number|no\b\.?|#))?|customer\s+po|p\.o\.(?:\s*#)?)"
        r"\s*:?\s*([A-Z0-9][A-Z0-9\-]*)",
        re.I,
    ),
]
SUBTOTAL_RE = re.compile(r"\b(sub-?\s?total|net\s+amount|amount\s+before\s+tax)\b", re.I)
TAX_RE = re.compile(r"\b(gst|hst|pst|qst|vat|sales\s+tax|tax)\b", re.I)
TOTAL_RE = re.compile(r"\b(total\s+due|amount\s+due|balance\s+due|grand\s+total|invoice\s+total|total)\b", re.I)
NOT_TOTAL_RE = re.compile(r"\b(sub-?\s?total|line\s+total)\b", re.I)
CURRENCY_RE = re.compile(r"\b(CAD|USD|EUR|GBP|AUD)\b")

QTY = r"(?P<qty>\d+(?:\.\d+)?)"
UNIT = rf"(?P<unit>{MONEY_RE})"
AMT = rf"(?P<amt>{MONEY_RE})"
LINE_DESC_FIRST = re.compile(rf"^(?P<desc>.+?)\s+{QTY}\s+{UNIT}\s+{AMT}(?:\s+[A-Z]{{3}})?$")
LINE_QTY_FIRST = re.compile(rf"^{QTY}\s+(?P<desc>.+?)\s+{UNIT}\s+{AMT}(?:\s+[A-Z]{{3}})?$")
HEADER_RE = re.compile(r"\b(qty|quantity)\b", re.I)
HEADER_PRICE_RE = re.compile(r"\b(amount|total|price|rate|unit)\b", re.I)


def _search(pattern: re.Pattern, lines: list[str]) -> re.Match | None:
    """Search line by line so a match can never run across a line break."""
    for line in lines:
        m = pattern.search(line)
        if m:
            return m
    return None


def _last_money(line: str) -> Decimal | None:
    amounts = re.findall(MONEY_RE, line)
    return parse_money(amounts[-1]) if amounts else None


class RulesExtractor:
    name = "rules"

    def extract(self, pdf_bytes: bytes, text: str | None = None) -> ExtractionResult:
        return self.extract_from_text(text if text is not None else extract_text(pdf_bytes))

    def extract_from_text(self, text: str) -> ExtractionResult:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        data = InvoiceData()
        conf: dict[str, float] = {}
        notes: list[str] = []

        # Vendor: the first line that isn't a document title like "INVOICE".
        for line in lines[:6]:
            candidate = re.sub(r"\s+(tax\s+)?invoice$", "", line, flags=re.I).strip()
            if candidate and not DOC_TITLE_RE.match(candidate):
                data.vendor_name = candidate
                conf["vendor_name"] = 0.9 if COMPANY_SUFFIX_RE.search(candidate) else 0.6
                break

        for pattern, score in INVOICE_NUMBER_PATTERNS:
            m = _search(pattern, lines)
            if m:
                data.invoice_number = m.group(1)
                conf["invoice_number"] = score
                break

        for pattern in INVOICE_DATE_PATTERNS:
            m = _search(pattern, lines)
            if m:
                parsed, score = parse_date(m.group(1))
                if parsed:
                    data.invoice_date, conf["invoice_date"] = parsed, score
                    if score < STRONG:
                        notes.append(f"Ambiguous date '{m.group(1)}' read as day-first")
                    break

        for pattern in DUE_DATE_PATTERNS:
            m = _search(pattern, lines)
            if m:
                parsed, score = parse_date(m.group(1))
                if parsed:
                    data.due_date, conf["due_date"] = parsed, score
                    break

        for pattern in PO_PATTERNS:
            m = _search(pattern, lines)
            if m:
                data.po_number = m.group(1)
                conf["po_number"] = STRONG
                break

        m = CURRENCY_RE.search(text)
        if m:
            data.currency, conf["currency"] = m.group(1), STRONG
        elif "$" in text:
            data.currency, conf["currency"] = "CAD", 0.5
            notes.append("No currency code found; assumed CAD from '$'")

        # Line items sit between the table header and the first totals line.
        header_idx = next(
            (i for i, ln in enumerate(lines) if HEADER_RE.search(ln) and HEADER_PRICE_RE.search(ln)),
            None,
        )
        totals_start = len(lines)
        if header_idx is not None:
            qty_first = bool(re.match(r"^\s*(qty|quantity)\b", lines[header_idx], re.I))
            line_re = LINE_QTY_FIRST if qty_first else LINE_DESC_FIRST
            for i in range(header_idx + 1, len(lines)):
                line = lines[i]
                if SUBTOTAL_RE.search(line) or (TOTAL_RE.search(line) and not NOT_TOTAL_RE.search(line)):
                    totals_start = i
                    break
                m = line_re.match(line)
                if m:
                    data.line_items.append(
                        LineItemData(
                            description=m.group("desc").strip(),
                            quantity=Decimal(m.group("qty")),
                            unit_price=parse_money(m.group("unit")),
                            amount=parse_money(m.group("amt")),
                        )
                    )
        else:
            notes.append("No line-item table header found")

        # Totals: scan the lines after the table.
        for line in lines[totals_start if header_idx is not None else 0 :]:
            amount = _last_money(line)
            if amount is None:
                continue
            if SUBTOTAL_RE.search(line):
                data.subtotal, conf["subtotal"] = amount, STRONG
            elif TOTAL_RE.search(line) and not NOT_TOTAL_RE.search(line):
                data.total, conf["total"] = amount, STRONG
            elif TAX_RE.search(line) and not re.search(r"tax\s+invoice", line, re.I):
                data.tax, conf["tax"] = amount, STRONG

        _score_consistency(data, conf)
        return ExtractionResult(data=data, confidence=conf, extractor=self.name, notes=notes)


def _score_consistency(data: InvoiceData, conf: dict[str, float]) -> None:
    """Numbers that agree with each other are more likely to be read correctly."""
    tol = Decimal("0.01")
    if data.subtotal is not None and data.tax is not None and data.total is not None:
        if abs(data.subtotal + data.tax - data.total) <= tol:
            for f in ("subtotal", "tax", "total"):
                conf[f] = max(conf.get(f, 0), 0.99)
    if data.line_items:
        amounts = [li.amount for li in data.line_items if li.amount is not None]
        if data.subtotal is not None and len(amounts) == len(data.line_items):
            conf["line_items"] = 0.95 if abs(sum(amounts) - data.subtotal) <= tol else 0.7
        else:
            conf["line_items"] = 0.7
