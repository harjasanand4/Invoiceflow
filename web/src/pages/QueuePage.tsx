import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { money, StatusPill } from "../components/ui";
import type { DocumentSummary, Stats } from "../types";

const TABS: { key: string; label: string; statuses: string }[] = [
  { key: "review", label: "Needs review", statuses: "needs_review" },
  { key: "auto", label: "Auto-approved", statuses: "auto_approved" },
  { key: "approved", label: "Approved", statuses: "approved" },
  { key: "rejected", label: "Rejected", statuses: "rejected" },
  { key: "failed", label: "Failed", statuses: "failed,queued,processing" },
  { key: "all", label: "All", statuses: "" },
];

export default function QueuePage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.key === params.get("tab")) ?? TABS[0];
  const [q, setQ] = useState(params.get("q") ?? "");
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, s] = await Promise.all([
        api.listDocuments({ status: tab.statuses, q: params.get("q") ?? "" }),
        api.stats(),
      ]);
      setDocs(list.documents);
      setTotal(list.total);
      setStats(s);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [tab.statuses, params]);

  useEffect(() => {
    load();
  }, [load]);

  // While the background worker is busy, refresh every few seconds.
  const busy = stats ? stats.by_status.queued + stats.by_status.processing > 0 : false;
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [busy, load]);

  const count = (statuses: string) =>
    stats ? statuses.split(",").reduce((n, s) => n + (stats.by_status[s as keyof Stats["by_status"]] ?? 0), 0) : null;

  async function handleFiles(files: File[]) {
    const pdfs = files.filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length) {
      setMessage("Only PDF files can be uploaded.");
      return;
    }
    setMessage(`Uploading ${pdfs.length} file(s)…`);
    try {
      const res = await api.upload(pdfs);
      const dupes = res.documents.filter((d) => d.duplicate_upload).length;
      const review = res.documents.filter((d) => d.status === "needs_review").length;
      const queued = res.documents.filter((d) => d.status === "queued").length;
      setMessage(
        (queued
          ? `Uploaded ${res.documents.length} file(s); the worker is processing them`
          : `Processed ${res.documents.length} file(s): ${review} need review`) +
          (dupes ? `, ${dupes} already uploaded before` : "") +
          (res.errors.length ? `, ${res.errors.length} rejected` : ""),
      );
      load();
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  return (
    <div
      className={`page ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        handleFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <div className="page-head">
        <div>
          <h1>Invoices</h1>
          <p className="muted">Drop PDFs anywhere on this page to upload them.</p>
        </div>
        <label className="btn btn-primary">
          Upload PDFs
          <input
            type="file"
            accept="application/pdf"
            multiple
            hidden
            onChange={(e) => {
              handleFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
        </label>
      </div>

      {message && (
        <div className="banner" onClick={() => setMessage(null)}>
          {message}
        </div>
      )}

      <div className="toolbar">
        <div className="tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={t.key === tab.key}
              className={`tab ${t.key === tab.key ? "active" : ""}`}
              onClick={() => setParams({ tab: t.key, ...(q ? { q } : {}) })}
            >
              {t.label}
              {t.statuses && count(t.statuses) !== null && <span className="count">{count(t.statuses)}</span>}
            </button>
          ))}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setParams({ tab: tab.key, ...(q ? { q } : {}) });
          }}
        >
          <input className="search" placeholder="Search vendor or invoice #" value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
      </div>

      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Vendor</th>
              <th>Invoice #</th>
              <th className="num">Total</th>
              <th>Issues</th>
              <th>Status</th>
              <th>Read by</th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id} onClick={() => navigate(`/documents/${d.id}`)} className="clickable">
                <td className="muted">{d.id}</td>
                <td>
                  <Link to={`/documents/${d.id}`} onClick={(e) => e.stopPropagation()}>
                    {d.vendor_name ?? <span className="muted">{d.filename}</span>}
                  </Link>
                </td>
                <td className="mono">{d.invoice_number ?? "—"}</td>
                <td className="num mono">{money(d.total, d.currency)}</td>
                <td>
                  {d.error_count > 0 && <span className="badge badge-error">{d.error_count} error{d.error_count > 1 ? "s" : ""}</span>}
                  {d.warning_count > 0 && <span className="badge badge-warning">{d.warning_count} warning{d.warning_count > 1 ? "s" : ""}</span>}
                  {d.error_count === 0 && d.warning_count === 0 && <span className="muted">—</span>}
                </td>
                <td>
                  <StatusPill status={d.status} />
                </td>
                <td className="muted small">{d.extractor_used ?? "—"}</td>
              </tr>
            ))}
            {!loading && docs.length === 0 && (
              <tr>
                <td colSpan={7} className="empty">
                  Nothing here.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {total > docs.length && <p className="muted small pad">Showing {docs.length} of {total}</p>}
      </div>
    </div>
  );
}
