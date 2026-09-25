"""Command-line helpers.

    python -m app.cli init-db
    python -m app.cli load-pos data/synthetic/purchase_orders.json
    python -m app.cli ingest data/synthetic/pdfs          # queue a folder of PDFs (copies, doesn't move)
    python -m app.cli demo                                # all of the above + process, for a quick demo
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db import Session, create_tables, init_engine
from app.extraction import build_extractor
from app.models import PurchaseOrder, to_cents
from app.processor import ingest_bytes, process_pending
from app.storage import build_storage


def load_pos(session, path: Path) -> int:
    items = json.loads(path.read_text())
    for item in items:
        po = session.execute(
            select(PurchaseOrder).where(PurchaseOrder.po_number == item["po_number"])
        ).scalar_one_or_none() or PurchaseOrder(po_number=item["po_number"])
        po.vendor_name = item["vendor_name"]
        po.amount_cents = to_cents(item["amount"])
        po.currency = item.get("currency", "CAD")
        po.status = item.get("status", "open")
        session.add(po)
    session.commit()
    return len(items)


def ingest_dir(session, storage, folder: Path) -> int:
    created = 0
    for path in sorted(folder.glob("*.pdf")):
        _, was_new = ingest_bytes(session, storage, path.read_bytes(), path.name, source="folder")
        created += int(was_new)
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    p = sub.add_parser("load-pos")
    p.add_argument("path", type=Path)
    p = sub.add_parser("ingest")
    p.add_argument("folder", type=Path)
    p = sub.add_parser("demo")
    p.add_argument("--dataset", type=Path, default=Path("data/synthetic"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings()
    init_engine(settings.database_url)
    create_tables()
    session = Session()
    storage = build_storage(settings)

    if args.cmd == "init-db":
        print("Tables created")
    elif args.cmd == "load-pos":
        print(f"Loaded {load_pos(session, args.path)} purchase orders")
    elif args.cmd == "ingest":
        print(f"Queued {ingest_dir(session, storage, args.folder)} new documents")
    elif args.cmd == "demo":
        load_pos(session, args.dataset / "purchase_orders.json")
        queued = ingest_dir(session, storage, args.dataset / "pdfs")
        processed = process_pending(session, storage, build_extractor(settings), settings, limit=10_000)
        print(f"Queued {queued}, processed {processed}. Start the API and open the UI to review.")


if __name__ == "__main__":
    main()
