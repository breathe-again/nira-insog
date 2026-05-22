/** Public page: someone clicked an invite link.
 *
 * URL: /accept-invite/:token
 *
 * Step 1: call api.checkInvite(token) → see org name, invitee email, expiry.
 * Step 2: invitee enters a password → api.acceptInvite(token, password).
 * Step 3: on success, send them to /login (they sign in with the email + the
 *         password they just set).
 *
 * This page is shown to UN-authenticated users, so it lives OUTSIDE the
 * ProtectedRoute wrapper in App.tsx.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, KeyRound, Loader2, Sparkles } from "lucide-react";
import { api } from "../api";
import type { InviteCheckOut } from "../types";

export default function AcceptInvite() {
  const { token = "" } = useParams<{ token: string }>();
  const navigate = useNavigate();

  const [check, setCheck] = useState<InviteCheckOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .checkInvite(token)
      .then((c) => {
        if (!cancelled) setCheck(c);
      })
      .catch((e) => {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Invite link is invalid.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const submit = useCallback(async () => {
    setSubmitError(null);
    if (password.length < 12) {
      setSubmitError("Password must be at least 12 characters.");
      return;
    }
    if (password !== confirm) {
      setSubmitError("Passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await api.acceptInvite(token, password);
      setDone(true);
      setTimeout(() => navigate("/login"), 1800);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Couldn't accept invite.");
    } finally {
      setSubmitting(false);
    }
  }, [token, password, confirm, navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50 p-6">
      <div className="w-full max-w-md rounded-2xl bg-white ring-1 ring-ink-200 shadow-sm p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-9 w-9 rounded-xl bg-brand-100 text-brand-700 flex items-center justify-center">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <div className="font-semibold text-ink-900">Nira Insig</div>
            <div className="text-xs text-ink-500">Accept your invitation</div>
          </div>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-ink-600">
            <Loader2 className="h-4 w-4 animate-spin" />
            Verifying invitation…
          </div>
        )}

        {error && !loading && (
          <div className="rounded-lg ring-1 ring-rose-200 bg-rose-50 text-rose-900 p-4 text-sm flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <div>
              <div className="font-medium">This invitation isn't valid.</div>
              <div className="text-xs mt-1">{error}</div>
              <div className="text-xs mt-2">
                Ask the founder to send a fresh invite link.
              </div>
            </div>
          </div>
        )}

        {check && !done && !error && (
          <>
            <div className="text-sm text-ink-700 mb-4">
              You've been invited to join{" "}
              <span className="font-semibold text-ink-900">{check.org_name}</span>{" "}
              as <span className="font-medium">{check.role}</span>.
            </div>
            <div className="rounded-lg bg-ink-50 p-3 text-xs text-ink-700 mb-4">
              Invitee email: <span className="font-mono">{check.email}</span>
              <br />
              Expires:{" "}
              {new Date(check.expires_at).toLocaleString(undefined, {
                day: "numeric",
                month: "short",
                year: "numeric",
                hour: "numeric",
                minute: "2-digit",
              })}
            </div>

            <div className="space-y-3">
              <PasswordField
                label="Choose a password"
                value={password}
                onChange={setPassword}
                autoFocus
              />
              <PasswordField
                label="Confirm password"
                value={confirm}
                onChange={setConfirm}
              />
              {submitError && (
                <div className="text-xs text-rose-700 flex items-start gap-1">
                  <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
                  {submitError}
                </div>
              )}
              <button
                onClick={submit}
                disabled={submitting || !password || !confirm}
                className="btn-primary w-full justify-center"
              >
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <KeyRound className="h-4 w-4" />
                )}
                Create account
              </button>
            </div>
            <div className="text-[11px] text-ink-500 mt-3">
              Minimum 12 characters. Your password is hashed with argon2id —
              we never see it in plain text.
            </div>
          </>
        )}

        {done && (
          <div className="rounded-lg ring-1 ring-emerald-200 bg-emerald-50 text-emerald-900 p-4 text-sm flex items-start gap-2">
            <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
            <div>
              Account created. Redirecting you to sign in…
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function PasswordField({
  label,
  value,
  onChange,
  autoFocus,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoFocus?: boolean;
}) {
  return (
    <label className="block">
      <span className="block text-[11px] uppercase tracking-wider text-ink-500 mb-1">
        {label}
      </span>
      <input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoFocus={autoFocus}
        autoComplete="new-password"
        className="w-full rounded-lg ring-1 ring-ink-200 bg-white px-3 py-2 text-sm focus:ring-2 focus:ring-brand-300 focus:outline-none"
      />
    </label>
  );
}
