export type Status =
  | "queued"
  | "processing"
  | "needs_review"
  | "auto_approved"
  | "approved"
  | "rejected"
  | "failed";

export interface DocumentSummary {
  id: number;
  filename: string;
  status: Status;
  source: string;
  vendor_name: string | null;
  invoice_number: string | null;
  total: string | null;
  currency: string | null;
  error_count: number;
  warning_count: number;
  extractor_used: string | null;
  created_at: string | null;
}

export interface LineItem {
  description: string;
  quantity: string | null;
  unit_price: string | null;
  amount: string | null;
}

export interface Invoice {
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  po_number: string | null;
  currency: string | null;
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  confidence: Record<string, number>;
  line_items: LineItem[];
  notes: string[];
}

export interface Issue {
  code: string;
  severity: "error" | "warning";
  field: string | null;
  message: string;
}

export interface HistoryEvent {
  action: string;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  note: string | null;
  reviewer: string;
  created_at: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  sha256: string;
  page_count: number;
  size_bytes: number;
  attempts: number;
  last_error: string | null;
  llm_calls: number;
  processing_ms: number | null;
  processed_at: string | null;
  reviewed_at: string | null;
  invoice: Invoice | null;
  issues: Issue[];
  history: HistoryEvent[];
  reopened_documents?: number[];
}

export interface Stats {
  by_status: Record<Status, number>;
  total_documents: number;
  touchless_rate: number | null;
  avg_processing_ms: number | null;
  llm_calls: number;
  approved_value: string | null;
  top_issues: { code: string; severity: string; count: number }[];
  corrections_by_field: { field: string; count: number }[];
}

export const HEADER_FIELDS = [
  "vendor_name",
  "invoice_number",
  "invoice_date",
  "due_date",
  "po_number",
  "currency",
  "subtotal",
  "tax",
  "total",
] as const;

export type HeaderField = (typeof HEADER_FIELDS)[number];
