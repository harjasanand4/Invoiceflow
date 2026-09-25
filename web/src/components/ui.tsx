import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Status } from "../types";

const STATUS_LABEL: Record<Status, string> = {
  queued: "Queued",
  processing: "Processing",
  needs_review: "Needs review",
  auto_approved: "Auto-approved",
  approved: "Approved",
  rejected: "Rejected",
  failed: "Failed",
};

export function statusLabel(s: Status) {
  return STATUS_LABEL[s] ?? s;
}

export function StatusPill({ status }: { status: Status }) {
  return <span className={`pill pill-${status}`}>{statusLabel(status)}</span>;
}

export function ConfidenceDot({ value }: { value: number | undefined }) {
  if (value === undefined) return <span className="conf conf-none" title="Not read" />;
  const level = value >= 0.95 ? "high" : value >= 0.85 ? "ok" : "low";
  return (
    <span className={`conf conf-${level}`} title={`Confidence ${(value * 100).toFixed(0)}%`}>
      {(value * 100).toFixed(0)}%
    </span>
  );
}

export function Modal(props: {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  requireNote?: boolean;
  onConfirm: (note: string) => void;
  onCancel: () => void;
}) {
  const [note, setNote] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const disabled = props.requireNote && !note.trim();
  return (
    <div className="modal-backdrop" onClick={props.onCancel}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>{props.title}</h3>
        {props.children}
        <textarea
          ref={ref}
          placeholder={props.requireNote ? "Reason (required, saved to the audit trail)" : "Note (optional)"}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
        />
        <div className="modal-actions">
          <button className="btn" onClick={props.onCancel}>
            Cancel
          </button>
          <button
            className={`btn ${props.danger ? "btn-danger" : "btn-primary"}`}
            disabled={disabled}
            onClick={() => props.onConfirm(note)}
          >
            {props.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function money(value: string | null, currency?: string | null) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  const formatted = n.toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${formatted} ${currency}` : formatted;
}

/** "PO_AMOUNT_EXCEEDED" -> "PO amount exceeded" */
export function issueLabel(code: string) {
  const text = code.toLowerCase().replaceAll("_", " ").replace(/\bpo\b/g, "PO");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
