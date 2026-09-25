import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { ConfidenceDot, issueLabel, Modal, StatusPill } from "../components/ui";
import { HEADER_FIELDS, type DocumentDetail, type HeaderField, type LineItem } from "../types";

const LABELS: Record<HeaderField, string> = {
  vendor_name: "Vendor",
  invoice_number: "Invoice #",
  invoice_date: "Invoice date",
  due_date: "Due date",
  po_number: "PO number",
  currency: "Currency",
  subtotal: "Subtotal",
  tax: "Tax",
  total: "Total",
};

type Form = Record<HeaderField, string>;

function toForm(doc: DocumentDetail): Form {
  const inv = doc.invoice;
  return Object.fromEntries(HEADER_FIELDS.map((f) => [f, inv?.[f] ?? ""])) as Form;
}

export default function DocumentPage() {
  const { id } = useParams();
  const docId = Number(id);
  const navigate = useNavigate();
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [items, setItems] = useState<LineItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [modal, setModal] = useState<"override" | "reject" | null>(null);

  const apply = useCallback((d: DocumentDetail) => {
    setDoc(d);
    setForm(toForm(d));
    setItems(d.invoice?.line_items.map((li) => ({ ...li })) ?? []);
  }, []);

  useEffect(() => {
    setError(null);
    setNotice(null);
    api.getDocument(docId).then(apply).catch((e) => setError(e.message));
  }, [docId, apply]);

  const changes = useMemo(() => {
    if (!doc?.invoice || !form) return {};
    const out: Record<string, unknown> = {};
    for (const f of HEADER_FIELDS) {
      const before = doc.invoice[f] ?? "";
      if (form[f] !== before) out[f] = form[f] === "" ? null : form[f];
    }
    if (JSON.stringify(items) !== JSON.stringify(doc.invoice.line_items)) out.line_items = items;
    return out;
  }, [doc, form, items]);
  const dirty = Object.keys(changes).length > 0;

  const issuesByField = useMemo(() => {
    const map: Record<string, "error" | "warning"> = {};
    for (const i of doc?.issues ?? []) {
      if (!i.field) continue;
      if (i.severity === "error" || !map[i.field]) map[i.field] = i.severity;
    }
    return map;
  }, [doc]);

  async function run(action: () => Promise<DocumentDetail>, success?: string) {
    setBusy(true);
    setError(null);
    try {
      const d = await action();
      apply(d);
      if (d.reopened_documents?.length) {
        setNotice(`Saved. Re-checking found problems in document(s) #${d.reopened_documents.join(", #")}; they're back in the review queue.`);
      } else if (success) setNotice(success);
      return d;
    } catch (e) {
      const err = e as ApiError;
      setError(err.message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function goToNext() {
    const list = await api.listDocuments({ status: "needs_review" });
    const next = list.documents.find((d) => d.id !== docId);
    navigate(next ? `/documents/${next.id}` : "/?tab=review");
  }

  async function approve(override = false, note = "") {
    if (dirty) {
      setError("Save or discard your corrections before approving.");
      return;
    }
    const hasErrors = doc?.issues.some((i) => i.severity === "error");
    if (hasErrors && !override) {
      setModal("override");
      return;
    }
    setModal(null);
    const d = await run(() => api.approve(docId, override, note));
    if (d) goToNext();
  }

  if (error && !doc) return <div className="page"><div className="banner banner-error">{error}</div></div>;
  if (!doc || !form) return <div className="page muted">Loading…</div>;

  const editable = doc.status === "needs_review" || doc.status === "auto_approved";
  const conf = doc.invoice?.confidence ?? {};
  const errors = doc.issues.filter((i) => i.severity === "error");
  const warnings = doc.issues.filter((i) => i.severity === "warning");

  return (
    <div className="page doc-page">
      <div className="page-head">
        <div>
          <Link to="/?tab=review" className="muted small">
            ← Back to queue
          </Link>
          <h1>
            {doc.invoice?.vendor_name ?? doc.filename} <StatusPill status={doc.status} />
          </h1>
          <p className="muted small">
            Document #{doc.id} · {doc.filename} · read by {doc.extractor_used ?? "—"}
            {doc.processing_ms !== null && ` in ${doc.processing_ms} ms`}
            {doc.llm_calls > 0 && ` · ${doc.llm_calls} LLM call${doc.llm_calls > 1 ? "s" : ""}`}
          </p>
        </div>
        <div className="actions">
          {doc.status !== "approved" && (
            <button className="btn" disabled={busy} onClick={() => run(() => api.reprocess(docId), "Reprocessed.")}>
              Reprocess
            </button>
          )}
          {editable && (
            <>
              <button className="btn btn-danger-outline" disabled={busy} onClick={() => setModal("reject")}>
                Reject
              </button>
              <button className="btn btn-primary" disabled={busy} onClick={() => approve()}>
                Approve &amp; next
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div className="banner banner-error">{error}</div>}
      {notice && <div className="banner" onClick={() => setNotice(null)}>{notice}</div>}
      {doc.last_error && <div className="banner banner-error">Last processing error: {doc.last_error}</div>}

      <div className="split">
        <div className="card viewer">
          <div className="viewer-bar">
            <span className="muted small">
              {doc.page_count} page{doc.page_count === 1 ? "" : "s"}
            </span>
            <a className="small" href={`/api/documents/${doc.id}/file`} target="_blank" rel="noreferrer">
              Open original PDF ↗
            </a>
          </div>
          <div className="viewer-pages">
            {Array.from({ length: doc.page_count }, (_, i) => (
              <img key={i} src={`/api/documents/${doc.id}/pages/${i + 1}.png`} alt={`Page ${i + 1} of the invoice`} loading="lazy" />
            ))}
          </div>
        </div>

        <div className="side">
          {(errors.length > 0 || warnings.length > 0) && (
            <div className="card">
              <h2>Checks</h2>
              <ul className="issues">
                {[...errors, ...warnings].map((i, n) => (
                  <li key={n} className={`issue issue-${i.severity}`}>
                    <strong>{issueLabel(i.code)}</strong>
                    <span>{i.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="card">
            <div className="card-head">
              <h2>Extracted fields</h2>
              {dirty && editable && (
                <div className="actions">
                  <button className="btn btn-small" onClick={() => apply(doc)}>
                    Discard
                  </button>
                  <button className="btn btn-small btn-primary" disabled={busy} onClick={() => run(() => api.correct(docId, changes), "Corrections saved and re-checked.")}>
                    Save corrections
                  </button>
                </div>
              )}
            </div>
            <div className="fields">
              {HEADER_FIELDS.map((f) => (
                <label key={f} className={`field ${issuesByField[f] ? `field-${issuesByField[f]}` : ""}`}>
                  <span className="field-label">
                    {LABELS[f]} <ConfidenceDot value={conf[f]} />
                  </span>
                  <input
                    type={f.endsWith("_date") ? "date" : "text"}
                    inputMode={["subtotal", "tax", "total"].includes(f) ? "decimal" : undefined}
                    value={form[f]}
                    disabled={!editable}
                    onChange={(e) => setForm({ ...form, [f]: e.target.value })}
                  />
                </label>
              ))}
            </div>
            {doc.invoice?.notes.map((n) => (
              <p key={n} className="muted small">
                Note: {n}
              </p>
            ))}
          </div>

          <div className="card">
            <div className="card-head">
              <h2>Line items</h2>
              <ConfidenceDot value={conf.line_items} />
            </div>
            <table className="table items">
              <thead>
                <tr>
                  <th>Description</th>
                  <th className="num">Qty</th>
                  <th className="num">Unit</th>
                  <th className="num">Amount</th>
                  {editable && <th />}
                </tr>
              </thead>
              <tbody>
                {items.map((li, i) => (
                  <tr key={i}>
                    {(["description", "quantity", "unit_price", "amount"] as const).map((k) => (
                      <td key={k} className={k === "description" ? "" : "num"}>
                        <input
                          value={li[k] ?? ""}
                          disabled={!editable}
                          onChange={(e) => {
                            const next = [...items];
                            next[i] = { ...li, [k]: e.target.value === "" && k !== "description" ? null : e.target.value };
                            setItems(next);
                          }}
                        />
                      </td>
                    ))}
                    {editable && (
                      <td>
                        <button className="icon-btn" title="Remove line" onClick={() => setItems(items.filter((_, j) => j !== i))}>
                          ×
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {editable && (
              <button
                className="btn btn-small"
                onClick={() => setItems([...items, { description: "", quantity: null, unit_price: null, amount: null }])}
              >
                + Add line
              </button>
            )}
          </div>

          {doc.history.length > 0 && (
            <div className="card">
              <h2>Audit trail</h2>
              <ul className="history">
                {doc.history.map((h, i) => (
                  <li key={i}>
                    <span className="muted small">{h.created_at ? new Date(h.created_at).toLocaleString() : ""}</span>{" "}
                    <strong>{h.reviewer}</strong> {h.action === "correct" ? `changed ${h.field}` : `${h.action}d`}
                    {h.action === "correct" && (
                      <span className="mono small">
                        {" "}
                        {h.old_value} → {h.new_value}
                      </span>
                    )}
                    {h.note && <em> “{h.note}”</em>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      {modal === "override" && (
        <Modal
          title="Approve despite errors?"
          confirmLabel="Approve anyway"
          requireNote
          onCancel={() => setModal(null)}
          onConfirm={(note) => approve(true, note)}
        >
          <ul className="issues">
            {errors.map((i, n) => (
              <li key={n} className="issue issue-error">
                {i.message}
              </li>
            ))}
          </ul>
        </Modal>
      )}
      {modal === "reject" && (
        <Modal
          title="Reject this invoice?"
          confirmLabel="Reject"
          danger
          requireNote
          onCancel={() => setModal(null)}
          onConfirm={async (note) => {
            setModal(null);
            const d = await run(() => api.reject(docId, note));
            if (d) goToNext();
          }}
        />
      )}
    </div>
  );
}
