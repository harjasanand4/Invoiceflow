"""Generate a labeled dataset of synthetic invoice PDFs.

Every PDF gets a matching JSON label containing the true field values, plus the
errors that were planted on purpose (wrong totals, duplicates, PO problems), so
the eval can measure both extraction accuracy and how many planted errors
validation catches.

Vendors, buyers and addresses are made up.

    python -m data.generate_invoices --count 80 --seed 42 --out data/synthetic
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

CENT = Decimal("0.01")


@dataclass
class Vendor:
    name: str
    address: str
    city: str
    province: str
    currency: str
    tax_label: str
    tax_rate: Decimal


VENDORS = [
    Vendor("Prairie Peak Supplies Ltd.", "4410 Kestrel Way NE", "Calgary, AB", "AB", "CAD", "GST", Decimal("0.05")),
    Vendor("Bow River Logistics Inc.", "88 Lantern Rd SE", "Calgary, AB", "AB", "CAD", "GST", Decimal("0.05")),
    Vendor("Northern Lights Office Co.", "210 Birchmount Cres", "Edmonton, AB", "AB", "CAD", "GST", Decimal("0.05")),
    Vendor("Foothills Industrial Parts Ltd.", "17 Ridgeview Dr", "Red Deer, AB", "AB", "CAD", "GST", Decimal("0.05")),
    Vendor("Maple Circuit Electronics Inc.", "950 Dundalk Ave", "Toronto, ON", "ON", "CAD", "HST", Decimal("0.13")),
    Vendor("Lakeshore Print & Sign Ltd.", "31 Harbourfront Ln", "Mississauga, ON", "ON", "CAD", "HST", Decimal("0.13")),
    Vendor("Tidewater Safety Equipment Ltd.", "6 Quayside St", "Halifax, NS", "NS", "CAD", "HST", Decimal("0.15")),
    Vendor("Coastline Janitorial Services Inc.", "402 Cedar Park Rd", "Burnaby, BC", "BC", "CAD", "GST", Decimal("0.05")),
    Vendor("Cascade Cloud Services LLC", "1200 Pinecrest Blvd", "Portland, OR", "OR", "USD", "Tax", Decimal("0")),
    Vendor("Chinook Catering Co.", "75 Sunridge Blvd NE", "Calgary, AB", "AB", "CAD", "GST", Decimal("0.05")),
]

CATALOG = [
    ("Ergonomic office chair", (180, 420), (1, 8)),
    ("Standing desk frame", (350, 700), (1, 4)),
    ("Printer toner cartridge", (60, 140), (2, 12)),
    ("Safety gloves (box of 50)", (18, 45), (2, 20)),
    ("Hi-vis vest, size L", (12, 30), (5, 25)),
    ("Freight - LTL shipment", (220, 900), (1, 3)),
    ("Pallet handling fee", (25, 60), (1, 10)),
    ("Consulting hours", (95, 175), (1, 40)),
    ("Cloud compute (vCPU-month)", (20, 45), (4, 64)),
    ("Object storage (TB-month)", (18, 30), (1, 20)),
    ("Catered lunch, per person", (18, 32), (10, 60)),
    ("Janitorial service visit", (140, 260), (1, 12)),
    ("Vinyl banner 3x8 ft", (70, 160), (1, 6)),
    ("USB-C docking station", (160, 320), (1, 10)),
    ("Hydraulic hose assembly", (45, 190), (1, 15)),
    ("First aid kit refill", (35, 90), (1, 8)),
]

BUYER = ("Harbor Point Energy Ltd.", "500 Centre St SW, Suite 900", "Calgary, AB T2P 0A1")
TEMPLATES = ["classic", "modern", "compact", "freeform"]
# "freeform" is a held-out layout: the rule extractor was not written against it.


def money(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(d: Decimal, commas: bool = False) -> str:
    return f"{d:,.2f}" if commas else f"{d:.2f}"


def fmt_qty(q: Decimal) -> str:
    return f"{q:f}".rstrip("0").rstrip(".") if "." in f"{q:f}" else f"{q:f}"


def make_invoice(rng: random.Random, idx: int, template: str) -> dict:
    vendor = rng.choice(VENDORS)
    items = []
    for desc, (lo, hi), (qlo, qhi) in rng.sample(CATALOG, rng.randint(1, 6)):
        unit = money(Decimal(str(rng.uniform(lo, hi))))
        if desc == "Consulting hours":
            qty = Decimal(str(rng.choice([x / 2 for x in range(qlo * 2, qhi * 2 + 1)])))
        else:
            qty = Decimal(rng.randint(qlo, qhi))
        items.append({"description": desc, "quantity": qty, "unit_price": unit, "amount": money(qty * unit)})

    subtotal = sum((i["amount"] for i in items), Decimal("0"))
    tax = money(subtotal * vendor.tax_rate)
    total = subtotal + tax

    inv_date = date(2026, 1, 5) + timedelta(days=rng.randint(0, 230))
    terms = rng.choice([15, 30, 30, 45])
    due = inv_date + timedelta(days=terms)

    if template == "classic":
        number = f"INV-2026-{rng.randint(100, 9999):04d}"
    elif template == "modern":
        number = str(rng.randint(10000, 99999))
    elif template == "compact":
        number = f"{rng.choice('ABCDEFGHJK')}{rng.randint(1, 9)}-{rng.randint(1000, 9999)}"
    else:
        number = f"B{rng.randint(100000, 999999)}"

    po = f"PO-{rng.randint(10000, 19999)}" if rng.random() < 0.7 else None

    return {
        "id": idx,
        "template": template,
        "vendor": vendor,
        "number": number,
        "date": inv_date,
        "due": due,
        "terms": terms,
        "po": po,
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "seeded_issues": [],
    }


# ---------------------------------------------------------------- rendering


def draw_classic(c: canvas.Canvas, inv: dict) -> None:
    v: Vendor = inv["vendor"]
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, 740, v.name)
    c.setFont("Helvetica", 10)
    c.drawString(50, 724, v.address)
    c.drawString(50, 711, v.city)
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(562, 740, "INVOICE")

    c.setFont("Helvetica", 10)
    rows = [("Invoice #:", inv["number"]), ("Invoice Date:", inv["date"].isoformat())]
    if inv["show_due"]:
        rows.append(("Due Date:", inv["due"].isoformat()))
    if inv["po"]:
        rows.append(("PO Number:", inv["po"]))
    y = 690
    for label, value in rows:
        c.drawString(390, y, label)
        c.drawRightString(562, y, value)
        y -= 14

    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, 640, "Bill To:")
    c.setFont("Helvetica", 10)
    for i, line in enumerate(BUYER):
        c.drawString(50, 626 - i * 13, line)

    y = 560
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Description")
    c.drawRightString(360, y, "Qty")
    c.drawRightString(460, y, "Unit Price")
    c.drawRightString(562, y, "Amount")
    c.line(50, y - 5, 562, y - 5)
    c.setFont("Helvetica", 10)
    for item in inv["items"]:
        y -= 18
        c.drawString(50, y, item["description"])
        c.drawRightString(360, y, fmt_qty(item["quantity"]))
        c.drawRightString(460, y, fmt(item["unit_price"], True))
        c.drawRightString(562, y, fmt(item["amount"], True))

    y -= 30
    rate_pct = fmt_qty(inv["vendor"].tax_rate * 100)
    totals = [
        ("Subtotal", inv["printed_subtotal"]),
        (f"{v.tax_label} ({rate_pct}%)", inv["printed_tax"]),
        ("Total", inv["printed_total"]),
    ]
    for label, value in totals:
        c.setFont("Helvetica-Bold" if label == "Total" else "Helvetica", 10)
        c.drawString(400, y, label)
        c.drawRightString(562, y, fmt(value, True))
        y -= 16
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(50, 80, f"All amounts in {v.currency}. Thank you for your business.")


def draw_modern(c: canvas.Canvas, inv: dict) -> None:
    v: Vendor = inv["vendor"]
    c.setFillGray(0.15)
    c.rect(0, 752, 612, 40, fill=1, stroke=0)
    c.setFillGray(1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, 765, v.name)
    c.setFillGray(0)

    c.setFont("Helvetica", 10)
    left = [f"Invoice No. {inv['number']}", f"Issued: {inv['date'].strftime('%b %d, %Y')}"]
    if inv["show_due"]:
        left.append(f"Payment due: {inv['due'].strftime('%b %d, %Y')}")
    if inv["po"]:
        left.append(f"Purchase Order: {inv['po']}")
    for i, line in enumerate(left):
        c.drawString(40, 720 - i * 15, line)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(360, 720, "Bill To")
    c.setFont("Helvetica", 10)
    for i, line in enumerate(BUYER):
        c.drawString(360, 705 - i * 13, line)

    y = 610
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Item")
    c.drawRightString(380, y, "Quantity")
    c.drawRightString(470, y, "Rate")
    c.drawRightString(572, y, "Line Total")
    c.setFont("Helvetica", 10)
    for item in inv["items"]:
        y -= 20
        c.drawString(40, y, item["description"])
        c.drawRightString(380, y, fmt_qty(item["quantity"]))
        c.drawRightString(470, y, "$" + fmt(item["unit_price"], True))
        c.drawRightString(572, y, "$" + fmt(item["amount"], True))

    y -= 34
    rate_pct = fmt_qty(v.tax_rate * 100)
    for label, value in [
        ("Sub-total", inv["printed_subtotal"]),
        (f"{v.tax_label} {rate_pct}%", inv["printed_tax"]),
        (f"Amount Due ({v.currency})", inv["printed_total"]),
    ]:
        c.setFont("Helvetica-Bold" if label.startswith("Amount") else "Helvetica", 11)
        c.drawString(380, y, label)
        c.drawRightString(572, y, "$" + fmt(value, True))
        y -= 18
    c.setFont("Helvetica", 8)
    c.drawString(40, 60, f"{v.address}, {v.city}")


def draw_compact(c: canvas.Canvas, inv: dict) -> None:
    v: Vendor = inv["vendor"]
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(306, 750, v.name)
    c.setFont("Helvetica", 9)
    c.drawCentredString(306, 736, f"{v.address}, {v.city}")
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(306, 712, "Tax Invoice")

    c.setFont("Helvetica", 10)
    c.drawString(50, 685, f"Ref: {inv['number']}")
    c.drawString(200, 685, f"Invoice Date: {inv['date'].strftime('%d/%m/%Y')}")
    c.drawString(420, 685, f"Terms: Net {inv['terms']}")
    if inv["po"]:
        c.drawString(50, 670, f"Customer PO: {inv['po']}")
    c.drawString(50, 650, f"Customer: {BUYER[0]}")

    y = 610
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Qty")
    c.drawString(90, y, "Description")
    c.drawRightString(460, y, "Unit")
    c.drawRightString(562, y, "Total")
    c.setFont("Helvetica", 10)
    for item in inv["items"]:
        y -= 16
        c.drawString(50, y, fmt_qty(item["quantity"]))
        c.drawString(90, y, item["description"])
        c.drawRightString(460, y, fmt(item["unit_price"], True))
        c.drawRightString(562, y, fmt(item["amount"], True))

    y -= 28
    rate_pct = fmt_qty(v.tax_rate * 100)
    for label, value in [
        ("Net Amount", inv["printed_subtotal"]),
        (f"{v.tax_label} {rate_pct}%", inv["printed_tax"]),
        ("TOTAL DUE", inv["printed_total"]),
    ]:
        c.setFont("Helvetica-Bold" if label == "TOTAL DUE" else "Helvetica", 10)
        c.drawString(360, y, label)
        c.drawRightString(562, y, f"{fmt(value, True)} {v.currency}")
        y -= 15


def draw_freeform(c: canvas.Canvas, inv: dict) -> None:
    """Held-out layout: the rule extractor was NOT written against this one.

    It uses different labels ("Bill Number", "Date of Issue", "Please pay"), a
    different date format and a different column order, to measure how well each
    extractor handles a layout it has never seen.
    """
    v: Vendor = inv["vendor"]
    c.setFont("Times-Roman", 11)
    c.drawString(60, 745, "Statement of charges from")
    c.setFont("Times-Bold", 17)
    c.drawString(60, 725, v.name.upper())
    c.setFont("Times-Roman", 10)
    c.drawString(60, 710, f"{v.address} | {v.city}")

    c.setFont("Times-Roman", 11)
    c.drawString(60, 675, f"Bill Number {inv['number']}")
    c.drawString(60, 660, f"Date of Issue {inv['date'].strftime('%d-%b-%Y')}")
    if inv["show_due"]:
        c.drawString(60, 645, f"Pay By {inv['due'].strftime('%d-%b-%Y')}")
    if inv["po"]:
        c.drawString(330, 675, f"Order Ref {inv['po']}")
    c.drawString(330, 660, f"Prepared for {BUYER[0]}")

    y = 600
    c.setFont("Times-Bold", 11)
    c.drawString(60, y, "Details")
    c.drawRightString(350, y, "Each")
    c.drawRightString(420, y, "Units")
    c.drawRightString(552, y, "Ext.")
    c.setFont("Times-Roman", 11)
    for item in inv["items"]:
        y -= 17
        c.drawString(60, y, item["description"])
        c.drawRightString(350, y, fmt(item["unit_price"], True))
        c.drawRightString(420, y, fmt_qty(item["quantity"]))
        c.drawRightString(552, y, fmt(item["amount"], True))

    y -= 30
    rate_pct = fmt_qty(v.tax_rate * 100)
    for label, value in [
        ("Pre-tax", inv["printed_subtotal"]),
        (f"{v.tax_label} @ {rate_pct}%", inv["printed_tax"]),
        (f"Please pay ({v.currency})", inv["printed_total"]),
    ]:
        c.setFont("Times-Bold" if label.startswith("Please") else "Times-Roman", 11)
        c.drawString(380, y, label)
        c.drawRightString(552, y, fmt(value, True))
        y -= 16


DRAWERS = {"classic": draw_classic, "modern": draw_modern, "compact": draw_compact, "freeform": draw_freeform}


def render(inv: dict, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle(f"Invoice {inv['number']}")
    DRAWERS[inv["template"]](c, inv)
    c.showPage()
    c.save()


def label_for(inv: dict, filename: str) -> dict:
    return {
        "file": filename,
        "template": inv["template"],
        "expected": {
            "vendor_name": inv["vendor"].name,
            "invoice_number": inv["number"],
            "invoice_date": inv["date"].isoformat(),
            # The compact template prints payment terms instead of a due date.
            "due_date": inv["due"].isoformat() if inv["show_due"] else None,
            "po_number": inv["po"],
            "currency": inv["vendor"].currency,
            "subtotal": fmt(inv["printed_subtotal"]),
            "tax": fmt(inv["printed_tax"]),
            "total": fmt(inv["printed_total"]),
            "line_items": [
                {
                    "description": i["description"],
                    "quantity": fmt_qty(i["quantity"]),
                    "unit_price": fmt(i["unit_price"]),
                    "amount": fmt(i["amount"]),
                }
                for i in inv["items"]
            ],
        },
        "seeded_issues": inv["seeded_issues"],
    }


def generate(count: int, seed: int, out: Path) -> None:
    rng = random.Random(seed)
    if out.exists():
        shutil.rmtree(out)
    (out / "pdfs").mkdir(parents=True)
    (out / "labels").mkdir(parents=True)

    invoices: list[dict] = []
    purchase_orders: dict[str, dict] = {}

    for idx in range(1, count + 1):
        template = TEMPLATES[(idx - 1) % len(TEMPLATES)]
        roll = rng.random()

        clean = [i for i in invoices if not i["seeded_issues"]]
        if roll < 0.06 and clean:
            # Duplicate: a clean invoice sent again, rendered in a different layout
            # (so the file hash differs and only field-level matching can catch it).
            original = rng.choice(clean)
            inv = dict(original)
            inv["id"] = idx
            inv["template"] = rng.choice([t for t in TEMPLATES if t != original["template"]])
            inv["seeded_issues"] = ["DUPLICATE_INVOICE"]
            inv["show_due"] = inv["template"] != "compact"
            invoices.append(inv)
            continue

        inv = make_invoice(rng, idx, template)
        inv["show_due"] = template != "compact"
        inv["printed_subtotal"] = inv["subtotal"]
        inv["printed_tax"] = inv["tax"]
        inv["printed_total"] = inv["total"]

        if roll < 0.13:
            inv["printed_total"] = inv["total"] + money(Decimal(str(rng.uniform(5, 60))))
            inv["seeded_issues"].append("TOTAL_MISMATCH")
        elif roll < 0.20:
            inv["printed_subtotal"] = inv["subtotal"] + money(Decimal(str(rng.uniform(10, 80))))
            inv["printed_tax"] = money(inv["printed_subtotal"] * inv["vendor"].tax_rate)
            inv["printed_total"] = inv["printed_subtotal"] + inv["printed_tax"]
            inv["seeded_issues"].append("LINE_ITEMS_SUM_MISMATCH")

        if inv["po"]:
            if 0.20 <= roll < 0.26:
                inv["seeded_issues"].append("PO_NOT_FOUND")  # PO never registered
            else:
                amount = inv["printed_total"] * Decimal(str(rng.uniform(1.0, 1.3)))
                if 0.26 <= roll < 0.32:
                    amount = inv["printed_total"] * Decimal("0.8")
                    inv["seeded_issues"].append("PO_AMOUNT_EXCEEDED")
                purchase_orders[inv["po"]] = {
                    "po_number": inv["po"],
                    "vendor_name": inv["vendor"].name,
                    "amount": fmt(money(amount)),
                    "currency": inv["vendor"].currency,
                }
        invoices.append(inv)

    for inv in invoices:
        filename = f"inv_{inv['id']:04d}.pdf"
        render(inv, out / "pdfs" / filename)
        (out / "labels" / f"inv_{inv['id']:04d}.json").write_text(
            json.dumps(label_for(inv, filename), indent=2)
        )

    (out / "purchase_orders.json").write_text(json.dumps(list(purchase_orders.values()), indent=2))
    seeded = sum(len(i["seeded_issues"]) for i in invoices)
    print(f"Wrote {len(invoices)} invoices ({seeded} seeded issues) and {len(purchase_orders)} POs to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=80)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/synthetic"))
    args = parser.parse_args()
    generate(args.count, args.seed, args.out)


if __name__ == "__main__":
    main()
