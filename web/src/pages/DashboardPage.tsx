import { useEffect, useState } from "react";
import { api } from "../api";
import { issueLabel, money, statusLabel } from "../components/ui";
import type { Stats, Status } from "../types";

const ORDER: Status[] = ["auto_approved", "approved", "needs_review", "rejected", "failed", "queued", "processing"];

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.stats().then(setStats).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="page"><div className="banner banner-error">{error}</div></div>;
  if (!stats) return <div className="page muted">Loading…</div>;

  const total = stats.total_documents || 1;
  const maxIssue = Math.max(1, ...stats.top_issues.map((i) => i.count));
  const maxCorr = Math.max(1, ...stats.corrections_by_field.map((c) => c.count));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="muted">How much work the pipeline is taking off people's plates.</p>
        </div>
        <a className="btn" href="/api/export.csv">
          Export approved (CSV)
        </a>
      </div>

      <div className="stats">
        <div className="stat card">
          <span className="stat-label">Documents</span>
          <span className="stat-value">{stats.total_documents}</span>
        </div>
        <div className="stat card">
          <span className="stat-label">Touchless rate</span>
          <span className="stat-value">{stats.touchless_rate === null ? "—" : `${(stats.touchless_rate * 100).toFixed(0)}%`}</span>
          <span className="muted small">approved with no human involved</span>
        </div>
        <div className="stat card">
          <span className="stat-label">Avg processing time</span>
          <span className="stat-value">{stats.avg_processing_ms ?? "—"} ms</span>
        </div>
        <div className="stat card">
          <span className="stat-label">LLM calls</span>
          <span className="stat-value">{stats.llm_calls}</span>
        </div>
        <div className="stat card">
          <span className="stat-label">Approved value</span>
          <span className="stat-value">{money(stats.approved_value)}</span>
        </div>
      </div>

      <div className="card">
        <h2>Where documents ended up</h2>
        <div className="stacked" role="img" aria-label="Documents by status">
          {ORDER.filter((s) => stats.by_status[s]).map((s) => (
            <div key={s} className={`seg seg-${s}`} style={{ width: `${(stats.by_status[s] / total) * 100}%` }} title={`${statusLabel(s)}: ${stats.by_status[s]}`} />
          ))}
        </div>
        <div className="legend">
          {ORDER.filter((s) => stats.by_status[s]).map((s) => (
            <span key={s}>
              <i className={`swatch seg-${s}`} /> {statusLabel(s)} <strong>{stats.by_status[s]}</strong>
            </span>
          ))}
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <h2>Most common issues</h2>
          {stats.top_issues.length === 0 && <p className="muted">No issues yet.</p>}
          {stats.top_issues.map((i) => (
            <div key={i.code + i.severity} className="bar-row">
              <span className="bar-label">{issueLabel(i.code)}</span>
              <div className="bar-track">
                <div className={`bar bar-${i.severity}`} style={{ width: `${(i.count / maxIssue) * 100}%` }} />
              </div>
              <span className="bar-value">{i.count}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Fields reviewers corrected</h2>
          <p className="muted small">Where extraction goes wrong most. Fix these first.</p>
          {stats.corrections_by_field.length === 0 && <p className="muted">No corrections yet.</p>}
          {stats.corrections_by_field.map((c) => (
            <div key={c.field} className="bar-row">
              <span className="bar-label">{c.field.replaceAll("_", " ")}</span>
              <div className="bar-track">
                <div className="bar" style={{ width: `${(c.count / maxCorr) * 100}%` }} />
              </div>
              <span className="bar-value">{c.count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
