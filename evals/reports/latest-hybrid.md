# Eval report: `hybrid` extractor

Run 2026-09-25T00:48:09+00:00 on 80 documents from `data/synthetic`

## Summary

| Metric | Value |
|---|---|
| Mean field accuracy | 98.8% |
| Documents fully correct | 98.8% |
| Line-item F1 | 99.2% |
| Planted errors caught | 22/22 (100.0%) |
| False alarms | 0 |
| Touchless (auto-approved) | 57 (71.2%) |
| Auto-approved that were correct | 100.0% |
| LLM calls per document | 0.525 |
| Time per document | 11029.7 ms |
| Failed documents | 1 |

## Field accuracy

| Field | Accuracy |
|---|---|
| vendor_name | 98.8% |
| invoice_number | 98.8% |
| invoice_date | 98.8% |
| due_date | 98.8% |
| po_number | 98.8% |
| currency | 98.8% |
| subtotal | 98.8% |
| tax | 98.8% |
| total | 98.8% |

## By layout

| Layout | Documents | Mean field accuracy |
|---|---|---|
| classic | 19 | 100.0% |
| compact | 20 | 100.0% |
| freeform (held out) | 19 | 94.7% |
| modern | 22 | 100.0% |
