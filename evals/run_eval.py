"""Evaluation harness: run the real pipeline over a labeled dataset and score it.

Measures:
- Field accuracy: per field, per layout, and "document fully correct".
- Line-item precision / recall / F1.
- Error detection: how many planted errors validation caught, and false alarms.
- Routing safety: of the documents auto-approved with no human involved, how many
  were actually correct. This is the number that matters most in production.
- Cost: LLM calls and processing time.

    python -m evals.run_eval --extractor rules
    python -m evals.run_eval --extractor hybrid --min-field-accuracy 0.95   # CI gate

Exits with status 1 if a --min-* threshold isn't met, so CI fails when a change
(a new prompt, a new model, a regex edit) makes extraction worse.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.cli import load_pos
from app.config import Settings
from app.db import Session, create_tables, init_engine
from app.extraction import build_extractor
from app.models import Document, DocumentStatus
from app.processor import ingest_bytes, process_document
from app.schemas import HEADER_FIELDS
from app.storage import LocalStorage

BUSINESS_ISSUES = {
    "TOTAL_MISMATCH",
    "LINE_ITEMS_SUM_MISMATCH",
    "DUPLICATE_INVOICE",
    "PO_NOT_FOUND",
    "PO_AMOUNT_EXCEEDED",
}


def norm_text(v) -> str | None:
    if v is None:
        return None
    return re.sub(r"\s+", " ", str(v)).strip().rstrip(".").lower() or None


def norm_money(v) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"))


def field_correct(field: str, expected, got) -> bool:
    if field in ("subtotal", "tax", "total"):
        return norm_money(expected) == norm_money(got)
    return norm_text(expected) == norm_text(got)


def _same_qty(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return Decimal(str(a)) == Decimal(str(b))


def line_items_score(expected: list[dict], got: list[dict]) -> tuple[int, int, int]:
    """Returns (true positives, predicted count, expected count).

    A predicted line counts as correct if its description, quantity and amount all match
    an expected line that hasn't already been matched.
    """
    remaining = list(expected)
    tp = 0
    for item in got:
        for i, exp in enumerate(remaining):
            if (
                norm_text(item.get("description")) == norm_text(exp["description"])
                and norm_money(item.get("amount")) == norm_money(exp["amount"])
                and _same_qty(item.get("quantity"), exp.get("quantity"))
            ):
                tp += 1
                remaining.pop(i)
                break
    return tp, len(got), len(expected)


def pct(n: float, d: float) -> float | None:
    return round(n / d, 4) if d else None


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:  # noqa: BLE001
        return None


def run(
    dataset: Path, extractor_name: str, limit: int | None = None, llm_client=None, progress: bool = False
) -> dict:
    labels = sorted((dataset / "labels").glob("*.json"))[:limit]
    if not labels:
        raise SystemExit(f"No labels found in {dataset / 'labels'}")

    with tempfile.TemporaryDirectory() as tmp:
        settings = replace(
            Settings(),
            database_url=f"sqlite:///{tmp}/eval.db",
            storage_backend="local",
            storage_dir=f"{tmp}/files",
            extractor=extractor_name,
        )
        init_engine(settings.database_url)
        create_tables()
        session = Session()
        storage = LocalStorage(settings.storage_dir)
        extractor = build_extractor(settings, llm_client=llm_client)
        if (dataset / "purchase_orders.json").exists():
            load_pos(session, dataset / "purchase_orders.json")

        field_hits: dict[str, int] = defaultdict(int)
        template_hits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        template_docs: dict[str, int] = defaultdict(int)
        li_tp = li_pred = li_exp = 0
        docs_fully_correct = 0
        seeded_total = seeded_caught = false_alarms = 0
        missed: list[dict] = []
        auto_approved = auto_approved_correct = 0
        unsafe_auto_approvals: list[str] = []
        failures: list[dict] = []
        per_doc: list[dict] = []
        llm_calls = 0
        started = time.perf_counter()

        if progress:
            print(f"Evaluating {len(labels)} invoices with the '{extractor_name}' extractor...", file=sys.stderr)
        for index, label_path in enumerate(labels, start=1):
            label = json.loads(label_path.read_text())
            if progress:
                print(f"  [{index}/{len(labels)}] {label['file']} ...", end="", file=sys.stderr, flush=True)
            pdf = (dataset / "pdfs" / label["file"]).read_bytes()
            doc, _ = ingest_bytes(session, storage, pdf, label["file"], source="eval")
            doc = process_document(session, storage, doc, extractor, settings)
            if doc.status == DocumentStatus.QUEUED:  # retry path, same as the worker
                while doc.status == DocumentStatus.QUEUED:
                    doc = process_document(session, storage, doc, extractor, settings)
            doc = session.get(Document, doc.id)
            llm_calls += doc.llm_calls
            if progress:
                elapsed = int(time.perf_counter() - started)
                print(f" {doc.status} ({doc.extractor_used or 'no result'}), {elapsed // 60}m{elapsed % 60:02d}s elapsed",
                      file=sys.stderr, flush=True)
            if doc.last_error and "QuotaExhaustedError" in doc.last_error:
                Session.remove()
                raise SystemExit(
                    f"\nStopped at {label['file']}: the LLM's daily quota is used up, so the rest of the run "
                    "would only fail.\nTry again after it resets, switch to a model with quota left "
                    "(GROQ_MODEL / GEMINI_MODEL in .env), or use a paid key."
                )

            expected, template = label["expected"], label["template"]
            template_docs[template] += 1
            if doc.status == DocumentStatus.FAILED or doc.invoice is None:
                failures.append({"file": label["file"], "error": doc.last_error})
                li_exp += len(expected["line_items"])
                seeded_total += len(label["seeded_issues"])
                continue

            got = doc.invoice.raw_extraction["data"]  # what the machine read, before any human edits
            wrong_fields = []
            for field in HEADER_FIELDS:
                ok = field_correct(field, expected[field], got.get(field))
                field_hits[field] += ok
                template_hits[template][field] += ok
                if not ok:
                    wrong_fields.append({"field": field, "expected": expected[field], "got": got.get(field)})

            tp, n_pred, n_exp = line_items_score(expected["line_items"], got.get("line_items", []))
            li_tp, li_pred, li_exp = li_tp + tp, li_pred + n_pred, li_exp + n_exp
            items_perfect = tp == n_exp == n_pred
            fully_correct = not wrong_fields and items_perfect
            docs_fully_correct += fully_correct

            raised = {i.code for i in doc.issues} & BUSINESS_ISSUES
            seeded = set(label["seeded_issues"])
            seeded_total += len(seeded)
            seeded_caught += len(seeded & raised)
            false_alarms += len(raised - seeded)
            for code in seeded - raised:
                missed.append({"file": label["file"], "issue": code})

            if doc.status == DocumentStatus.AUTO_APPROVED:
                auto_approved += 1
                if fully_correct and not seeded:
                    auto_approved_correct += 1
                else:
                    unsafe_auto_approvals.append(label["file"])

            per_doc.append({
                "file": label["file"],
                "template": template,
                "status": doc.status,
                "extractor": doc.extractor_used,
                "fully_correct": fully_correct,
                "wrong_fields": wrong_fields,
                "line_items": {"correct": tp, "predicted": n_pred, "expected": n_exp},
                "seeded_issues": sorted(seeded),
                "raised_issues": sorted({i.code for i in doc.issues}),
            })

        elapsed = time.perf_counter() - started
        Session.remove()

    n = len(labels)
    precision = pct(li_tp, li_pred)
    recall = pct(li_tp, li_exp)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else 0.0
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "dataset": str(dataset),
        "extractor": extractor_name,
        "documents": n,
        "failed_documents": failures,
        "field_accuracy": {f: pct(field_hits[f], n) for f in HEADER_FIELDS},
        "mean_field_accuracy": pct(sum(field_hits.values()), n * len(HEADER_FIELDS)),
        "document_fully_correct": pct(docs_fully_correct, n),
        "line_items": {"precision": precision, "recall": recall, "f1": f1},
        "by_template": {
            t: {
                "documents": template_docs[t],
                "mean_field_accuracy": pct(sum(template_hits[t].values()), template_docs[t] * len(HEADER_FIELDS)),
            }
            for t in sorted(template_docs)
        },
        "error_detection": {
            "seeded": seeded_total,
            "caught": seeded_caught,
            "recall": pct(seeded_caught, seeded_total),
            "false_alarms": false_alarms,
            "missed": missed,
        },
        "routing": {
            "auto_approved": auto_approved,
            "touchless_rate": pct(auto_approved, n),
            "auto_approved_precision": pct(auto_approved_correct, auto_approved),
            "unsafe_auto_approvals": unsafe_auto_approvals,
        },
        "cost": {
            "llm_calls": llm_calls,
            "llm_calls_per_doc": pct(llm_calls, n),
            "seconds_total": round(elapsed, 2),
            "ms_per_doc": round(elapsed / n * 1000, 1),
        },
        "documents_detail": per_doc,
    }


def fmt_pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def to_markdown(r: dict) -> str:
    lines = [
        f"# Eval report: `{r['extractor']}` extractor",
        "",
        f"Run {r['run_at']} on {r['documents']} documents from `{r['dataset']}`"
        + (f" (commit `{r['git_commit']}`)" if r["git_commit"] else ""),
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Mean field accuracy | {fmt_pct(r['mean_field_accuracy'])} |",
        f"| Documents fully correct | {fmt_pct(r['document_fully_correct'])} |",
        f"| Line-item F1 | {fmt_pct(r['line_items']['f1'])} |",
        f"| Planted errors caught | {r['error_detection']['caught']}/{r['error_detection']['seeded']} ({fmt_pct(r['error_detection']['recall'])}) |",
        f"| False alarms | {r['error_detection']['false_alarms']} |",
        f"| Touchless (auto-approved) | {r['routing']['auto_approved']} ({fmt_pct(r['routing']['touchless_rate'])}) |",
        f"| Auto-approved that were correct | {fmt_pct(r['routing']['auto_approved_precision'])} |",
        f"| LLM calls per document | {r['cost']['llm_calls_per_doc']} |",
        f"| Time per document | {r['cost']['ms_per_doc']} ms |",
        f"| Failed documents | {len(r['failed_documents'])} |",
        "",
        "## Field accuracy",
        "",
        "| Field | Accuracy |",
        "|---|---|",
        *[f"| {f} | {fmt_pct(v)} |" for f, v in r["field_accuracy"].items()],
        "",
        "## By layout",
        "",
        "| Layout | Documents | Mean field accuracy |",
        "|---|---|---|",
        *[
            f"| {t}{' (held out)' if t == 'freeform' else ''} | {v['documents']} | {fmt_pct(v['mean_field_accuracy'])} |"
            for t, v in r["by_template"].items()
        ],
    ]
    if r["routing"]["unsafe_auto_approvals"]:
        lines += ["", "## Auto-approved but wrong (investigate these)", ""]
        lines += [f"- {f}" for f in r["routing"]["unsafe_auto_approvals"]]
    if r["error_detection"]["missed"]:
        lines += ["", "## Planted errors that were missed", ""]
        lines += [f"- {m['file']}: {m['issue']}" for m in r["error_detection"]["missed"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--extractor", default="rules", choices=["rules", "llm", "hybrid"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report-dir", type=Path, default=Path("evals/reports"))
    parser.add_argument("--min-field-accuracy", type=float, help="Fail if mean field accuracy is below this (0-1)")
    parser.add_argument("--min-error-recall", type=float, help="Fail if planted-error recall is below this (0-1)")
    parser.add_argument("--min-auto-approved-precision", type=float,
                        help="Fail if any share of auto-approved documents were wrong beyond this (0-1)")
    args = parser.parse_args()

    import logging

    logging.basicConfig(level=logging.WARNING)
    report = run(args.dataset, args.extractor, args.limit, progress=True)
    md = to_markdown(report)
    print(md)

    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (args.report_dir / f"{stamp}-{args.extractor}.json").write_text(json.dumps(report, indent=2, default=str))
    (args.report_dir / f"latest-{args.extractor}.md").write_text(md)

    failed = []
    if args.min_field_accuracy is not None and (report["mean_field_accuracy"] or 0) < args.min_field_accuracy:
        failed.append(f"mean field accuracy {fmt_pct(report['mean_field_accuracy'])} < {fmt_pct(args.min_field_accuracy)}")
    if args.min_error_recall is not None and (report["error_detection"]["recall"] or 0) < args.min_error_recall:
        failed.append(f"error recall {fmt_pct(report['error_detection']['recall'])} < {fmt_pct(args.min_error_recall)}")
    p = report["routing"]["auto_approved_precision"]
    if args.min_auto_approved_precision is not None and p is not None and p < args.min_auto_approved_precision:
        failed.append(f"auto-approved precision {fmt_pct(p)} < {fmt_pct(args.min_auto_approved_precision)}")
    if failed:
        print("EVAL GATE FAILED: " + "; ".join(failed), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
