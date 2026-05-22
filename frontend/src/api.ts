/** Thin API client for the Nira Insig backend.
 *
 * Auth: we send `credentials: "include"` on every request so the
 * httpOnly access_token / refresh_token cookies travel with the call.
 * On a 401, the client tries ONE refresh against /api/auth/refresh; if
 * that fails too, it bubbles a NotAuthenticated error up — the
 * AuthProvider catches it and bounces to /login.
 *
 * No tokens are ever read from JS — everything stays in httpOnly cookies.
 */

import type {
  ApiHealth,
  AskOut,
  AuthMeOut,
  BackfillEmbeddingsOut,
  BackfillHashesOut,
  CategoryDetailOut,
  DashboardSummaryOut,
  DeleteDuplicateOut,
  DocumentDetailOut,
  DocumentListOut,
  DocumentOut,
  DuplicateClustersOut,
  InsightListOut,
  InvestmentActivityOut,
  LearningStatusOut,
  RetrainOut,
  SearchOut,
  TokensOut,
} from "./types";

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

export class NotAuthenticatedError extends Error {
  constructor(msg = "not authenticated") {
    super(msg);
    this.name = "NotAuthenticatedError";
  }
}

/** Thrown by uploadDocument() when the backend rejects a 409 duplicate. */
export class DuplicateUploadError extends Error {
  existingDocumentId?: string;
  existingFilename?: string;
  uploadedAt?: string;
  constructor(
    msg: string,
    existingDocumentId?: string,
    existingFilename?: string,
    uploadedAt?: string,
  ) {
    super(msg);
    this.name = "DuplicateUploadError";
    this.existingDocumentId = existingDocumentId;
    this.existingFilename = existingFilename;
    this.uploadedAt = uploadedAt;
  }
}

let _refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  // De-dupe concurrent refresh attempts.
  if (_refreshInFlight) return _refreshInFlight;
  _refreshInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      return res.ok;
    } catch {
      return false;
    } finally {
      _refreshInFlight = null;
    }
  })();
  return _refreshInFlight;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  opts: { retryOn401?: boolean } = { retryOn401: true },
): Promise<T> {
  const doFetch = () =>
    fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });

  let res = await doFetch();

  if (res.status === 401 && opts.retryOn401 && !path.startsWith("/api/auth/")) {
    const ok = await tryRefresh();
    if (ok) {
      res = await doFetch();
    }
  }

  if (res.status === 401) {
    throw new NotAuthenticatedError();
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} on ${path}: ${text || res.statusText}`);
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => request<ApiHealth>("/api/health"),

  // ---------- Auth ----------
  signup: (org_name: string, email: string, password: string) =>
    request<TokensOut>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ org_name, email, password }),
    }),
  login: (email: string, password: string) =>
    request<TokensOut>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<AuthMeOut>("/api/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  // ---------- Documents ----------
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
    const doFetch = () =>
      fetch(`${API_BASE}/api/documents`, {
        method: "POST",
        body: form,
        credentials: "include",
        // NOTE: don't set Content-Type — the browser sets the multipart boundary.
      });
    let res = await doFetch();
    if (res.status === 401) {
      const ok = await tryRefresh();
      if (ok) res = await doFetch();
    }
    if (res.status === 401) throw new NotAuthenticatedError();
    if (res.status === 409) {
      // Duplicate detected — surface a structured error so the UI can link
      // to the existing document instead of showing a red banner.
      let detail: {
        message?: string;
        existing_document_id?: string;
        existing_filename?: string;
        uploaded_at?: string;
      } = {};
      try {
        const body = (await res.json()) as { detail?: typeof detail };
        detail = body.detail ?? {};
      } catch {
        // ignore
      }
      throw new DuplicateUploadError(
        detail.message ?? "This file has already been uploaded.",
        detail.existing_document_id,
        detail.existing_filename,
        detail.uploaded_at,
      );
    }
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`Upload failed (${res.status}): ${text || res.statusText}`);
    }
    return (await res.json()) as DocumentOut;
  },

  getDocument: (id: string) => request<DocumentDetailOut>(`/api/documents/${id}`),

  patchDocument: (
    id: string,
    body: { document_type?: string; vendor_id?: string; category?: string },
  ) =>
    request<{ updated: string[]; document_id: string }>(`/api/documents/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  listDuplicates: () =>
    request<DuplicateClustersOut>("/api/documents/duplicates"),

  deleteAsDuplicate: (id: string) =>
    request<DeleteDuplicateOut>(`/api/documents/${id}/delete-as-duplicate`, {
      method: "POST",
    }),

  backfillHashes: (limit = 500) =>
    request<BackfillHashesOut>(
      `/api/documents/backfill-hashes?limit=${limit}`,
      { method: "POST" },
    ),

  dashboardSummary: (params: { from?: string; to?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.from) q.set("from_date", params.from);
    if (params.to) q.set("to_date", params.to);
    const qs = q.toString();
    return request<DashboardSummaryOut>(
      `/api/dashboard/summary${qs ? `?${qs}` : ""}`,
    );
  },

  categoryDetail: (
    category: string,
    params: { from?: string; to?: string; limit?: number } = {},
  ) => {
    const q = new URLSearchParams({ category });
    if (params.from) q.set("from_date", params.from);
    if (params.to) q.set("to_date", params.to);
    if (params.limit) q.set("limit", String(params.limit));
    return request<CategoryDetailOut>(
      `/api/dashboard/category-detail?${q.toString()}`,
    );
  },

  investmentActivity: (params: { from?: string; to?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.from) q.set("from_date", params.from);
    if (params.to) q.set("to_date", params.to);
    const qs = q.toString();
    return request<InvestmentActivityOut>(
      `/api/dashboard/investment-activity${qs ? `?${qs}` : ""}`,
    );
  },

  // ---------- Vendors ----------
  patchVendor: (
    id: string,
    body: {
      name?: string;
      default_expense_category?: string;
      gstin?: string;
      add_alias?: string;
    },
  ) => request<{ updated: string[]; vendor_id: string }>(`/api/vendors/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  }),
  mergeVendor: (winnerId: string, loserId: string) =>
    request<{ winner_id: string; loser_id: string }>(
      `/api/vendors/${winnerId}/merge`,
      {
        method: "POST",
        body: JSON.stringify({ loser_id: loserId }),
      },
    ),

  // ---------- Insights ----------
  listInsights: (
    params: {
      severity?: string;
      type?: string;
      include_dismissed?: boolean;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const q = new URLSearchParams();
    if (params.severity) q.set("severity", params.severity);
    if (params.type) q.set("type", params.type);
    if (params.include_dismissed) q.set("include_dismissed", "true");
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));
    const qs = q.toString();
    return request<InsightListOut>(`/api/insights${qs ? `?${qs}` : ""}`);
  },
  dismissInsight: (id: string) =>
    request<unknown>(`/api/insights/${id}/dismiss`, { method: "POST" }),
  patchInsight: (id: string, body: { severity?: string; mute_vendor?: boolean }) =>
    request<{ updated: string[]; insight_id: string }>(`/api/insights/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  // ---------- Learning ----------
  learningStatus: () => request<LearningStatusOut>("/api/learning/status"),
  retrain: () => request<RetrainOut>("/api/learning/retrain", { method: "POST" }),
  backfillEmbeddings: () =>
    request<BackfillEmbeddingsOut>("/api/learning/backfill-embeddings", {
      method: "POST",
    }),

  // ---------- Semantic search ----------
  search: (q: string, limit = 20) => {
    const params = new URLSearchParams({ q, limit: String(limit) });
    return request<SearchOut>(`/api/search?${params.toString()}`);
  },

  // ---------- Q&A ----------
  ask: (question: string) =>
    request<AskOut>("/api/qa/ask", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
};

export { API_BASE };
