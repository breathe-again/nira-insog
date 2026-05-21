/** Login page. Posts to /api/auth/login. */

import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Lock, Mail, Loader2 } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";

export default function Login() {
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const fromPath = (location.state as { from?: string } | null)?.from ?? "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already logged in? Bounce to the destination.
  if (user) {
    navigate(fromPath, { replace: true });
    return null;
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim().toLowerCase(), password);
      navigate(fromPath, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50 p-4">
      <div className="w-full max-w-md bg-white rounded-2xl shadow-sm border border-ink-200 p-8">
        <div className="mb-6 text-center">
          <div className="inline-flex h-10 w-10 rounded-2xl bg-brand-600 text-white items-center justify-center mb-3">
            <Lock className="h-5 w-5" />
          </div>
          <h1 className="text-xl font-semibold text-ink-900">Welcome back</h1>
          <p className="text-sm text-ink-500 mt-1">Sign in to Nira Insig</p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-ink-700">Email</span>
            <div className="mt-1 relative">
              <Mail className="absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-xl border border-ink-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-600"
                placeholder="you@example.com"
              />
            </div>
          </label>

          <label className="block">
            <span className="text-sm font-medium text-ink-700">Password</span>
            <div className="mt-1 relative">
              <Lock className="absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
              <input
                type="password"
                required
                autoComplete="current-password"
                minLength={1}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-xl border border-ink-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-600"
                placeholder="••••••••"
              />
            </div>
          </label>

          {error && (
            <div className="text-xs text-rose-600 whitespace-pre-line">{error}</div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-brand-600 text-white px-4 py-2.5 text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            Sign in
          </button>
        </form>

        <div className="mt-6 text-center text-sm text-ink-500">
          No account?{" "}
          <Link to="/signup" className="text-brand-700 font-medium hover:underline">
            Create one
          </Link>
        </div>
      </div>
    </div>
  );
}
