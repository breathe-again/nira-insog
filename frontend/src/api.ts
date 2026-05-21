/** Thin API client for the Nira Insig backend. */

import type {
  ApiHealth,
  DashboardSummaryOut,
  DocumentDetailOut,
  DocumentListOut,
  DocumentOut,
} from "./types";

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} on ${path}: ${text || res.statusText}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<ApiHealth>("/api/health"),

  listDocuments: (params: { limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));
    const qs = q.toString();
    return request<DocumentListOut>(`/api/documents${qs ? `?${qs}` : ""}`);
  },

  uploadDocument: async (file: File): Promise<DocumentOut> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/api/documents`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`Upload failed (${res.status}): ${text || res.statusText}`);
    }
    return (await res.json()) as DocumentOut;
  },

  getDocument: (id: string) => request<DocumentDetailOut>(`/api/documents/${id}`),

  dashboardSummary: () => request<DashboardSummaryOut>("/api/dashboard/summary"),
};

export { API_BASE };
