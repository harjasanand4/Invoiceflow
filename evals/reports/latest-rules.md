# Eval report: `rules` extractor

Run 2026-09-24T20:07:33+00:00 on 80 documents from `data/synthetic`

## Summary

| Metric | Value |
|---|---|
| Mean field accuracy | 82.4% |
| Documents fully correct | 76.2% |
| Line-item F1 | 86.0% |
| Planted errors caught | 18/22 (81.8%) |
| False alarms | 0 |
| Touchless (auto-approved) | 27 (33.8%) |
| Auto-approved that were correct | 96.3% |
| LLM calls per document | 0.0 |
| Time per document | 26.1 ms |
| Failed documents | 0 |

## Field accuracy

| Field | Accuracy |
|---|---|
| vendor_name | 76.2% |
| invoice_number | 76.2% |
| invoice_date | 76.2% |
| due_date | 76.2% |
| po_number | 83.8% |
| currency | 100.0% |
| subtotal | 76.2% |
| tax | 100.0% |
| total | 76.2% |

## By layout

| Layout | Documents | Mean field accuracy |
|---|---|---|
| classic | 19 | 100.0% |
| compact | 20 | 100.0% |
| freeform (held out) | 19 | 25.7% |
| modern | 22 | 100.0% |

## Auto-approved but wrong (investigate these)

- inv_0049.pdf

## Planted errors that were missed

- inv_0012.pdf: PO_NOT_FOUND
- inv_0044.pdf: PO_AMOUNT_EXCEEDED
- inv_0048.pdf: DUPLICATE_INVOICE
- inv_0049.pdf: DUPLICATE_INVOICE
