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
  AuthMeOut,
  DashboardSummaryOut,
  DocumentDetailOut,
  DocumentListOut,
  DocumentOut,
  InsightListOut,
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

  dashboardSummary: () => request<DashboardSummaryOut>("/api/dashboard/summary"),

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
};

export { API_BASE };
