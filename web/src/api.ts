import type { DocumentDetail, DocumentSummary, Invoice, Stats } from "./types";

export class ApiError extends Error {
  status: number;
  body: Record<string, unknown>;
  constructor(status: number, body: Record<string, unknown>) {
    super(typeof body.error === "string" ? body.error : `Request failed (${status})`);
    this.status = status;
    this.body = body;
  }
}

const REVIEWER_KEY = "invoiceflow.reviewer";

export function getReviewer(): string {
  try {
    return localStorage.getItem(REVIEWER_KEY) || "reviewer";
  } catch {
    return "reviewer";
  }
}

export function setReviewer(name: string) {
  try {
    localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    /* storage unavailable; fall back to the default name */
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Reviewer", getReviewer());
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

export const api = {
  listDocuments: (params: { status?: string; q?: string; page?: number }) => {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.q) qs.set("q", params.q);
    if (params.page) qs.set("page", String(params.page));
    return request<{ documents: DocumentSummary[]; total: number }>(`/api/documents?${qs}`);
  },
  getDocument: (id: number) => request<DocumentDetail>(`/api/documents/${id}`),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return request<{ documents: (DocumentSummary & { duplicate_upload: boolean })[]; errors: { filename: string; error: string }[] }>(
      "/api/documents",
      { method: "POST", body: form },
    );
  },
  correct: (id: number, changes: Partial<Invoice>) =>
    request<DocumentDetail>(`/api/documents/${id}/invoice`, { method: "PUT", body: JSON.stringify(changes) }),
  approve: (id: number, override = false, note = "") =>
    request<DocumentDetail>(`/api/documents/${id}/approve`, { method: "POST", body: JSON.stringify({ override, note }) }),
  reject: (id: number, note: string) =>
    request<DocumentDetail>(`/api/documents/${id}/reject`, { method: "POST", body: JSON.stringify({ note }) }),
  reprocess: (id: number) => request<DocumentDetail>(`/api/documents/${id}/reprocess`, { method: "POST", body: "{}" }),
  stats: () => request<Stats>("/api/stats"),
};
