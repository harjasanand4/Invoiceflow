-- Reporting views for finance / accounts payable (PostgreSQL).
-- Load with:  psql "$DATABASE_URL" -f sql/reports.sql
-- Money is stored in cents; the views convert to dollars.
-- Safe to re-run: each view is dropped and recreated.

-- Spend per vendor, approved invoices only.
DROP VIEW IF EXISTS v_vendor_spend;
CREATE VIEW v_vendor_spend AS
SELECT
    i.vendor_name,
    i.currency,
    COUNT(*)                                   AS invoices,
    (SUM(i.total_cents) / 100.0)::numeric(14,2)  AS total_spend,
    ROUND(AVG(i.total_cents) / 100.0, 2)       AS avg_invoice,
    MIN(i.invoice_date)                        AS first_invoice,
    MAX(i.invoice_date)                        AS last_invoice
FROM invoices i
JOIN documents d ON d.id = i.document_id
WHERE d.status IN ('approved', 'auto_approved')
GROUP BY i.vendor_name, i.currency;

-- How much of each purchase order has been billed (invoices that aren't rejected, including ones still in review).
DROP VIEW IF EXISTS v_po_utilization;
CREATE VIEW v_po_utilization AS
SELECT
    po.po_number,
    po.vendor_name,
    po.currency,
    (po.amount_cents / 100.0)::numeric(14,2)                       AS po_amount,
    (COALESCE(SUM(i.total_cents), 0) / 100.0)::numeric(14,2)       AS billed,
    ((po.amount_cents - COALESCE(SUM(i.total_cents), 0)) / 100.0)::numeric(14,2) AS remaining,
    ROUND(100.0 * COALESCE(SUM(i.total_cents), 0) / NULLIF(po.amount_cents, 0), 1) AS pct_billed,
    COUNT(i.id)                                                    AS invoices
FROM purchase_orders po
LEFT JOIN invoices i  ON i.po_number = po.po_number
LEFT JOIN documents d ON d.id = i.document_id AND d.status <> 'rejected'
WHERE i.id IS NULL OR d.id IS NOT NULL
GROUP BY po.po_number, po.vendor_name, po.currency, po.amount_cents;

-- Review queue, oldest first, with how long each item has been waiting.
DROP VIEW IF EXISTS v_review_queue;
CREATE VIEW v_review_queue AS
SELECT
    d.id,
    d.filename,
    i.vendor_name,
    i.invoice_number,
    (i.total_cents / 100.0)::numeric(14,2)                  AS total,
    d.created_at,
    ROUND(EXTRACT(EPOCH FROM (now() - d.created_at)) / 3600.0, 1) AS hours_waiting,
    COUNT(v.id) FILTER (WHERE v.severity = 'error')         AS errors,
    STRING_AGG(DISTINCT v.code, ', ')                       AS issue_codes
FROM documents d
LEFT JOIN invoices i          ON i.document_id = d.id
LEFT JOIN validation_issues v ON v.document_id = d.id
WHERE d.status = 'needs_review'
GROUP BY d.id, d.filename, i.vendor_name, i.invoice_number, i.total_cents, d.created_at
ORDER BY d.created_at;

-- Payables coming due in the next 30 days (approved but not yet paid).
DROP VIEW IF EXISTS v_upcoming_payments;
CREATE VIEW v_upcoming_payments AS
SELECT
    i.due_date,
    i.vendor_name,
    i.invoice_number,
    i.currency,
    (i.total_cents / 100.0)::numeric(14,2) AS amount,
    i.due_date - CURRENT_DATE AS days_until_due
FROM invoices i
JOIN documents d ON d.id = i.document_id
WHERE d.status IN ('approved', 'auto_approved')
  AND i.due_date BETWEEN CURRENT_DATE - 30 AND CURRENT_DATE + 30
ORDER BY i.due_date;

-- Weekly automation metrics: volume, touchless rate, and how often humans corrected fields.
DROP VIEW IF EXISTS v_weekly_automation;
CREATE VIEW v_weekly_automation AS
WITH docs AS (
    SELECT
        DATE_TRUNC('week', created_at)::date                              AS week,
        COUNT(*)                                                          AS documents,
        ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'auto_approved') / COUNT(*), 1) AS touchless_pct,
        COUNT(*) FILTER (WHERE status = 'failed')                         AS failed,
        ROUND(AVG(processing_ms))                                         AS avg_processing_ms,
        SUM(llm_calls)                                                    AS llm_calls
    FROM documents
    GROUP BY 1
),
corrections AS (
    SELECT DATE_TRUNC('week', created_at)::date AS week, COUNT(*) AS field_corrections
    FROM review_events
    WHERE action = 'correct'
    GROUP BY 1
)
SELECT docs.*, COALESCE(corrections.field_corrections, 0) AS field_corrections
FROM docs
LEFT JOIN corrections USING (week)
ORDER BY week DESC;
