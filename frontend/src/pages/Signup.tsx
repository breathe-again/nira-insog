/** Signup page. Creates an Org + first user in one shot.
 *
 * Password rules surfaced to the user (mirror of backend policy):
 *   - 12+ characters
 *   - not on the common-passwords list
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Building2, Lock, Mail, Loader2 } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";

export default function Signup() {
  const { signup, user } = useAuth();
  const navigate = useNavigate();

  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (user) {
    navigate("/", { replace: true });
    return null;
  }

  const pwOk = password.length >= 12;
  const matchOk = confirm.length > 0 && password === confirm;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!pwOk) {
      setError("Password must be at least 12 characters.");
      return;
    }
    if (!matchOk) {
      setError("Passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await signup(orgName.trim(), email.trim().toLowerCase(), password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Signup failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50 p-4">
      <div className="w-full max-w-md bg-white rounded-2xl shadow-sm border border-ink-200 p-8">
        <div className="mb-6 text-center">
          <div className="inline-flex h-10 w-10 rounded-2xl bg-brand-600 text-white items-center justify-center mb-3">
            <Building2 className="h-5 w-5" />
          </div>
          <h1 className="text-xl font-semibold text-ink-900">Start your trial</h1>
          <p className="text-sm text-ink-500 mt-1">
            Create your organization in 30 seconds
          </p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-ink-700">Company name</span>
            <div className="mt-1 relative">
              <Building2 className="absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
              <input
                type="text"
                required
                minLength={2}
                maxLength={120}
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                className="w-full rounded-xl border border-ink-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-600"
                placeholder="Acme Trading Co"
              />
            </div>
          </label>

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
                autoComplete="new-password"
                minLength={12}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-xl border border-ink-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-600"
                placeholder="12+ characters"
              />
            </div>
            <div className={`mt-1 text-xs ${pwOk ? "text-emerald-600" : "text-ink-500"}`}>
              {pwOk ? "✓ Length OK" : "Need at least 12 characters"}
            </div>
          </label>

          <label className="block">
            <span className="text-sm font-medium text-ink-700">Confirm password</span>
            <div className="mt-1 relative">
              <Lock className="absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
              <input
                type="password"
                required
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="w-full rounded-xl border border-ink-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-600"
                placeholder="Re-enter password"
              />
            </div>
            {confirm.length > 0 && (
              <div className={`mt-1 text-xs ${matchOk ? "text-emerald-600" : "text-rose-600"}`}>
                {matchOk ? "✓ Matches" : "Passwords don't match"}
              </div>
            )}
          </label>

          {error && (
            <div className="text-xs text-rose-600 whitespace-pre-line">{error}</div>
          )}

          <button
            type="submit"
            disabled={submitting || !pwOk || !matchOk}
            className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-brand-600 text-white px-4 py-2.5 text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            Create account
          </button>
        </form>

        <div className="mt-6 text-center text-sm text-ink-500">
          Already have an account?{" "}
          <Link to="/login" className="text-brand-700 font-medium hover:underline">
            Sign in
          </Link>
        </div>
      </div>
    </div>
  );
}
