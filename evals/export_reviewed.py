"""Turn human-reviewed documents into new eval test cases.

Every invoice a reviewer approved is ground truth: a person checked every field.
Exporting them grows the eval set with real documents over time, so the eval
covers the layouts you actually receive instead of only synthetic ones.

    python -m evals.export_reviewed --out data/reviewed
    python -m evals.run_eval --dataset data/reviewed --extractor hybrid
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db import Session, create_tables, init_engine
from app.models import Document, DocumentStatus
from app.serializers import invoice_json
from app.storage import build_storage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/reviewed"))
    parser.add_argument("--corrected-only", action="store_true",
                        help="Only export documents where a reviewer changed at least one field")
    args = parser.parse_args()

    settings = Settings()
    init_engine(settings.database_url)
    create_tables()
    storage = build_storage(settings)
    session = Session()
    (args.out / "pdfs").mkdir(parents=True, exist_ok=True)
    (args.out / "labels").mkdir(parents=True, exist_ok=True)

    docs = session.execute(
        select(Document).where(Document.status == DocumentStatus.APPROVED).order_by(Document.id)
    ).scalars().all()
    exported = 0
    for doc in docs:
        corrected = any(e.action == "correct" for e in doc.review_events)
        if args.corrected_only and not corrected:
            continue
        inv = invoice_json(doc.invoice)
        name = f"doc_{doc.id:05d}"
        (args.out / "pdfs" / f"{name}.pdf").write_bytes(storage.read(doc.storage_key))
        label = {
            "file": f"{name}.pdf",
            "template": "reviewed",
            "expected": {k: v for k, v in inv.items() if k not in ("confidence", "notes")},
            # Errors a reviewer overrode are not "planted", so none are expected here.
            "seeded_issues": [],
        }
        (args.out / "labels" / f"{name}.json").write_text(json.dumps(label, indent=2))
        exported += 1
    print(f"Exported {exported} reviewed documents to {args.out}")


if __name__ == "__main__":
    main()
