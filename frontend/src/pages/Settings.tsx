/** Settings page.
 *
 * Four real sections, all backed by live data:
 *   1. Workspace — org name + plan from /api/auth/me
 *   2. Team     — current user; future "Invite" once the endpoint lands
 *   3. Integrations — actual status of each connector we've shipped
 *   4. Security — change password, sign out, future 2FA / sessions
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronRight,
  Clipboard,
  ClipboardCheck,
  KeyRound,
  Loader2,
  LogOut,
  Mail,
  Monitor,
  Pencil,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Trash2,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import TopBar from "../components/TopBar";
import SectionCard from "../components/SectionCard";
import { api } from "../api";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "../lib/cn";
import type {
  InviteOut,
  MemberOut,
  SessionInfoOut,
  TeamOverviewOut,
} from "../types";

type IntegrationStatus = "live" | "beta" | "planned";

interface Integration {
  name: string;
  status: IntegrationStatus;
  hint: string;
}

// Source-of-truth list — keep in sync with what actually exists in the
// codebase. Mark `live` only when end-to-end ingestion works.
const INTEGRATIONS: Integration[] = [
  {
    name: "PDF / image upload",
    status: "live",
    hint: "Drag any bank statement, invoice, or receipt into Inbox",
  },
  {
    name: "CSV bank statement",
    status: "live",
    hint: "Same dropzone, instant parse — no OCR",
  },
  {
    name: "Tally Day Book XML",
    status: "live",
    hint: "Export from Tally Gateway → Display → Day Book → Alt+E",
  },
  {
    name: "Semantic search (pgvector)",
    status: "live",
    hint: "Search bank txns + invoices + receipts by meaning",
  },
  {
    name: "Q&A (LLM over your books)",
    status: "live",
    hint: "Ask plain-English questions on /ask",
  },
  {
    name: "Tax intelligence",
    status: "live",
    hint: "GSTIN check, advance tax, TDS draft on /tax",
  },
  {
    name: "Duplicate review",
    status: "live",
    hint: "SHA-256 dedupe at upload + fingerprint review on /duplicates",
  },
  {
    name: "Tally auto-sync agent",
    status: "planned",
    hint: "Desktop helper polling localhost:9000 every 15 min",
  },
  {
    name: "Account Aggregator (Setu)",
    status: "planned",
    hint: "Auto-pull bank statements via RBI AA framework",
  },
  {
    name: "Zoho Books bi-directional",
    status: "planned",
    hint: "OAuth, push expenses + pull invoices",
  },
  {
    name: "GSTR-2B reconciliation",
    status: "planned",
    hint: "Match purchase invoices against GSTN report",
  },
  {
    name: "WhatsApp Business inbox",
    status: "planned",
    hint: "Forward an invoice to a number; we parse it",
  },
  {
    name: "Email-to-inbox",
    status: "planned",
    hint: "Each org gets a unique address to bcc",
  },
  {
    name: "Slack alerts",
    status: "planned",
    hint: "Anomaly + cash-position warnings into a channel",
  },
];


export default function Settings() {
  const { user, logout, refresh } = useAuth();

  return (
    <>
      <TopBar
        title="Settings"
        subtitle="Workspace · team · sessions · integrations · security"
      />

      <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <WorkspaceSection user={user} onSaved={refresh} />
        <TeamSection user={user} />
        <SessionsSection />
        <SecuritySection onLogout={logout} />
        <IntegrationsSection />
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Workspace
// ---------------------------------------------------------------------------

function WorkspaceSection({
  user,
  onSaved,
}: {
  user: ReturnType<typeof useAuth>["user"];
  onSaved: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(user?.org_name ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync the input when the user object changes (e.g. fresh page load).
  useEffect(() => {
    if (!editing) setName(user?.org_name ?? "");
  }, [user?.org_name, editing]);

  const save = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await api.patchOrg({ name });
      await onSaved();
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  }, [name, onSaved]);

  const canEdit = user?.role === "founder";

  return (
    <SectionCard title="Workspace" subtitle="Organization details">
      <ul className="space-y-3">
        <li className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-ink-100 text-ink-600 flex items-center justify-center shrink-0">
            <Building2 className="h-4 w-4" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[11px] text-ink-500 uppercase tracking-wider">
              Organization name
            </div>
            {!editing ? (
              <div className="text-sm text-ink-900 truncate">
                {user?.org_name ?? "—"}
              </div>
            ) : (
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={busy}
                autoFocus
                maxLength={200}
                className="mt-1 w-full rounded-lg ring-1 ring-ink-200 bg-white px-2.5 py-1.5 text-sm focus:ring-2 focus:ring-brand-300 focus:outline-none"
              />
            )}
            {error && (
              <div className="text-xs text-rose-700 mt-1 flex items-start gap-1">
                <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
                {error}
              </div>
            )}
          </div>
          {canEdit && (
            <div className="shrink-0 flex items-center gap-1">
              {!editing ? (
                <button
                  onClick={() => setEditing(true)}
                  className="btn-ghost text-xs"
                  title="Rename organization"
                >
                  <Pencil className="h-3 w-3" />
                  Edit
                </button>
              ) : (
                <>
                  <button
                    onClick={() => {
                      setEditing(false);
                      setName(user?.org_name ?? "");
                      setError(null);
                    }}
                    disabled={busy}
                    className="btn-ghost text-xs"
                  >
                    <X className="h-3 w-3" />
                    Cancel
                  </button>
                  <button
                    onClick={save}
                    disabled={busy || !name.trim() || name === user?.org_name}
                    className="btn-primary text-xs"
                  >
                    {busy ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Check className="h-3 w-3" />
                    )}
                    Save
                  </button>
                </>
              )}
            </div>
          )}
        </li>
        <SettingRow
          Icon={ShieldCheck}
          label="Plan"
          value={(user?.org_plan ?? "—").toUpperCase()}
          badge={user?.org_plan === "trial" ? "TRIAL" : undefined}
        />
        <SettingRow
          Icon={Mail}
          label="Email-to-inbox"
          value="Not yet configured"
          muted
        />
      </ul>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Team
// ---------------------------------------------------------------------------

function TeamSection({ user }: { user: ReturnType<typeof useAuth>["user"] }) {
  const [overview, setOverview] = useState<TeamOverviewOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setOverview(await api.teamOverview());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const isFounder = user?.role === "founder";
  const members = overview?.members ?? [];
  const pending = overview?.pending_invites ?? [];

  return (
    <SectionCard
      title="Team"
      subtitle={
        loading
          ? "Loading…"
          : `${members.length} member${members.length === 1 ? "" : "s"}` +
            (pending.length ? ` · ${pending.length} pending invite${pending.length === 1 ? "" : "s"}` : "")
      }
      action={
        isFounder ? (
          <button
            onClick={() => setInviteOpen(true)}
            className="btn-primary text-xs"
          >
            <UserPlus className="h-3 w-3" />
            Invite
          </button>
        ) : null
      }
    >
      {error && (
        <div className="rounded-lg bg-rose-50 ring-1 ring-rose-200 text-rose-900 p-3 text-sm mb-3">
          {error}
        </div>
      )}

      <ul className="space-y-2">
        {members.map((m) => (
          <MemberRow key={m.id} m={m} isYou={m.id === user?.user_id} />
        ))}
      </ul>

      {pending.length > 0 && (
        <>
          <div className="text-[11px] uppercase tracking-wider text-ink-500 mt-4 mb-2">
            Pending invites
          </div>
          <ul className="space-y-2">
            {pending.map((inv) => (
              <PendingInviteRow
                key={inv.id}
                inv={inv}
                onChanged={load}
                canRevoke={isFounder}
              />
            ))}
          </ul>
        </>
      )}

      {inviteOpen && (
        <InviteForm
          onClose={() => setInviteOpen(false)}
          onCreated={() => {
            void load();
            // Keep the form open so the founder can see + copy the link.
          }}
        />
      )}
    </SectionCard>
  );
}

function MemberRow({ m, isYou }: { m: MemberOut; isYou: boolean }) {
  return (
    <li
      className={cn(
        "flex items-center gap-3 rounded-lg ring-1 p-3",
        isYou ? "ring-emerald-100 bg-emerald-50/40" : "ring-ink-100 bg-white",
      )}
    >
      <div
        className={cn(
          "h-9 w-9 rounded-lg flex items-center justify-center shrink-0",
          isYou ? "bg-emerald-100 text-emerald-700" : "bg-ink-100 text-ink-600",
        )}
      >
        <Users className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-ink-900 truncate">{m.email}</div>
        <div className="text-[11px] text-ink-500 mt-0.5 uppercase tracking-wider">
          {m.role}
          {m.last_login_at && (
            <span className="ml-2 normal-case">
              · last seen {new Date(m.last_login_at).toLocaleDateString()}
            </span>
          )}
        </div>
      </div>
      {isYou && <span className="chip bg-emerald-100 text-emerald-700">You</span>}
    </li>
  );
}

function PendingInviteRow({
  inv,
  onChanged,
  canRevoke,
}: {
  inv: InviteOut;
  onChanged: () => Promise<void>;
  canRevoke: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState(false);

  const copyLink = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(inv.invite_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      window.prompt("Copy this link:", inv.invite_url);
    }
  }, [inv.invite_url]);

  const revoke = useCallback(async () => {
    if (!window.confirm(`Revoke invite for ${inv.email}?`)) return;
    setRevoking(true);
    try {
      await api.revokeInvite(inv.id);
      await onChanged();
    } finally {
      setRevoking(false);
    }
  }, [inv, onChanged]);

  return (
    <li className="rounded-lg ring-1 ring-amber-100 bg-amber-50/40 p-3 space-y-2">
      <div className="flex items-center gap-3">
        <div className="h-9 w-9 rounded-lg bg-amber-100 text-amber-700 flex items-center justify-center shrink-0">
          <UserPlus className="h-4 w-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-ink-900 truncate">
            {inv.email}
          </div>
          <div className="text-[11px] text-ink-500 mt-0.5 uppercase tracking-wider">
            {inv.role} · expires {new Date(inv.expires_at).toLocaleDateString()}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 text-[11px] bg-white ring-1 ring-ink-200 rounded px-2 py-1 truncate">
          {inv.invite_url}
        </code>
        <button
          onClick={copyLink}
          className="btn bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50 shrink-0"
          title="Copy link"
        >
          {copied ? (
            <ClipboardCheck className="h-3 w-3 text-emerald-600" />
          ) : (
            <Clipboard className="h-3 w-3" />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
        {canRevoke && (
          <button
            onClick={revoke}
            disabled={revoking}
            className="btn bg-rose-50 text-rose-700 ring-1 ring-rose-200 hover:bg-rose-100 shrink-0"
            title="Revoke invite"
          >
            {revoking ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Trash2 className="h-3 w-3" />
            )}
          </button>
        )}
      </div>
    </li>
  );
}

function InviteForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"member" | "accountant">("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdLink, setCreatedLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const submit = useCallback(async () => {
    setError(null);
    if (!email.includes("@")) {
      setError("Please enter a valid email address.");
      return;
    }
    setBusy(true);
    try {
      const inv = await api.createInvite({ email: email.trim().toLowerCase(), role });
      setCreatedLink(inv.invite_url);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create invite.");
    } finally {
      setBusy(false);
    }
  }, [email, role, onCreated]);

  const copyLink = useCallback(async () => {
    if (!createdLink) return;
    try {
      await navigator.clipboard.writeText(createdLink);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      window.prompt("Copy this link:", createdLink);
    }
  }, [createdLink]);

  return (
    <div className="mt-4 rounded-lg ring-1 ring-brand-200 bg-brand-50/40 p-3 space-y-3">
      {createdLink ? (
        <>
          <div className="text-sm text-ink-900 font-medium flex items-center gap-2">
            <Check className="h-4 w-4 text-emerald-600" />
            Invite created for {email}
          </div>
          <div className="text-xs text-ink-600">
            Share this link with your teammate. It expires in 7 days.
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-[11px] bg-white ring-1 ring-ink-200 rounded px-2 py-1 truncate">
              {createdLink}
            </code>
            <button
              onClick={copyLink}
              className="btn bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50 shrink-0"
            >
              {copied ? (
                <ClipboardCheck className="h-3 w-3 text-emerald-600" />
              ) : (
                <Clipboard className="h-3 w-3" />
              )}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <button onClick={onClose} className="btn-ghost text-xs">
            Done
          </button>
        </>
      ) : (
        <>
          <div className="text-sm font-medium text-ink-900">Invite a teammate</div>
          <label className="block">
            <span className="block text-[11px] uppercase tracking-wider text-ink-500 mb-1">
              Email
            </span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
              placeholder="teammate@company.com"
              className="w-full rounded-lg ring-1 ring-ink-200 bg-white px-3 py-2 text-sm focus:ring-2 focus:ring-brand-300 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="block text-[11px] uppercase tracking-wider text-ink-500 mb-1">
              Role
            </span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as "member" | "accountant")}
              className="w-full rounded-lg ring-1 ring-ink-200 bg-white px-3 py-2 text-sm"
            >
              <option value="member">Member</option>
              <option value="accountant">Accountant (CA review access)</option>
            </select>
          </label>
          {error && (
            <div className="text-xs text-rose-700 flex items-start gap-1">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              {error}
            </div>
          )}
          <div className="flex items-center justify-end gap-2">
            <button onClick={onClose} className="btn-ghost text-xs" disabled={busy}>
              Cancel
            </button>
            <button
              onClick={submit}
              disabled={busy || !email}
              className="btn-primary"
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <UserPlus className="h-3.5 w-3.5" />
              )}
              Create invite link
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Active sessions
// ---------------------------------------------------------------------------

function SessionsSection() {
  const [sessions, setSessions] = useState<SessionInfoOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.listSessions();
      setSessions(result.sessions);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = useCallback(
    async (id: string) => {
      if (!window.confirm("Sign out this device?")) return;
      setBusyId(id);
      try {
        await api.revokeSession(id);
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Couldn't revoke session.");
      } finally {
        setBusyId(null);
      }
    },
    [load],
  );

  const revokeOthers = useCallback(async () => {
    if (!window.confirm("Sign out every other device? You'll stay signed in here.")) return;
    setBusyId("__others__");
    try {
      await api.revokeOtherSessions();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't revoke.");
    } finally {
      setBusyId(null);
    }
  }, [load]);

  const others = sessions.filter((s) => !s.is_current);

  return (
    <SectionCard
      title="Active sessions"
      subtitle={
        loading
          ? "Loading…"
          : `${sessions.length} device${sessions.length === 1 ? "" : "s"} signed in`
      }
      action={
        others.length > 0 ? (
          <button
            onClick={revokeOthers}
            disabled={busyId === "__others__"}
            className="btn bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50 text-xs"
          >
            {busyId === "__others__" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <LogOut className="h-3 w-3" />
            )}
            Sign out others
          </button>
        ) : null
      }
    >
      {error && (
        <div className="rounded-lg bg-rose-50 ring-1 ring-rose-200 text-rose-900 p-3 text-sm mb-3">
          {error}
        </div>
      )}
      <ul className="space-y-2">
        {sessions.map((s) => (
          <SessionRow
            key={s.id}
            s={s}
            onRevoke={() => revoke(s.id)}
            busy={busyId === s.id}
          />
        ))}
        {!loading && sessions.length === 0 && (
          <li className="text-sm text-ink-500 italic">No active sessions.</li>
        )}
      </ul>
    </SectionCard>
  );
}

function parseUserAgent(ua: string | null): { label: string; icon: typeof Monitor } {
  if (!ua) return { label: "Unknown device", icon: Monitor };
  const u = ua.toLowerCase();
  let browser = "Browser";
  if (u.includes("chrome") && !u.includes("edg/")) browser = "Chrome";
  else if (u.includes("safari") && !u.includes("chrome")) browser = "Safari";
  else if (u.includes("firefox")) browser = "Firefox";
  else if (u.includes("edg/")) browser = "Edge";
  let os = "device";
  if (u.includes("mac os x") || u.includes("macintosh")) os = "Mac";
  else if (u.includes("windows")) os = "Windows";
  else if (u.includes("linux")) os = "Linux";
  else if (u.includes("iphone") || u.includes("ipad")) os = "iOS";
  else if (u.includes("android")) os = "Android";
  const isMobile = u.includes("iphone") || u.includes("android");
  return {
    label: `${browser} on ${os}`,
    icon: isMobile ? Smartphone : Monitor,
  };
}

function SessionRow({
  s,
  onRevoke,
  busy,
}: {
  s: SessionInfoOut;
  onRevoke: () => void;
  busy: boolean;
}) {
  const { label, icon: Icon } = parseUserAgent(s.user_agent);
  return (
    <li
      className={cn(
        "flex items-center gap-3 rounded-lg ring-1 p-3",
        s.is_current ? "ring-emerald-100 bg-emerald-50/40" : "ring-ink-100 bg-white",
      )}
    >
      <div
        className={cn(
          "h-9 w-9 rounded-lg flex items-center justify-center shrink-0",
          s.is_current ? "bg-emerald-100 text-emerald-700" : "bg-ink-100 text-ink-600",
        )}
      >
        <Icon className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-ink-900 truncate flex items-center gap-2">
          {label}
          {s.is_current && (
            <span className="chip bg-emerald-100 text-emerald-700">This device</span>
          )}
        </div>
        <div className="text-[11px] text-ink-500 mt-0.5">
          {s.ip_address && <span>IP {s.ip_address}</span>}
          {s.last_used_at && (
            <span className="ml-2">
              · last used {new Date(s.last_used_at).toLocaleString()}
            </span>
          )}
        </div>
      </div>
      {!s.is_current && (
        <button
          onClick={onRevoke}
          disabled={busy}
          className="btn bg-rose-50 text-rose-700 ring-1 ring-rose-200 hover:bg-rose-100 shrink-0 text-xs"
        >
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <LogOut className="h-3 w-3" />}
          Sign out
        </button>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Integrations
// ---------------------------------------------------------------------------

function IntegrationsSection() {
  const live = INTEGRATIONS.filter((i) => i.status === "live");
  const planned = INTEGRATIONS.filter((i) => i.status !== "live");

  return (
    <SectionCard
      title="Integrations"
      subtitle={`${live.length} live · ${planned.length} planned`}
      className="lg:col-span-2"
    >
      <div className="text-[11px] uppercase tracking-wider text-emerald-700 mb-2">
        Live
      </div>
      <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 mb-5">
        {live.map((i) => (
          <IntegrationCard key={i.name} integration={i} />
        ))}
      </ul>

      <div className="text-[11px] uppercase tracking-wider text-ink-500 mb-2">
        Planned
      </div>
      <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {planned.map((i) => (
          <IntegrationCard key={i.name} integration={i} />
        ))}
      </ul>
    </SectionCard>
  );
}

function IntegrationCard({ integration }: { integration: Integration }) {
  const palette =
    integration.status === "live"
      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
      : integration.status === "beta"
        ? "bg-amber-50 text-amber-700 ring-amber-200"
        : "bg-ink-50 text-ink-500 ring-ink-200";
  return (
    <li className="rounded-xl ring-1 ring-ink-200 bg-white p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-ink-900 truncate">
          {integration.name}
        </span>
        <span className={cn("chip ring-1", palette)}>
          {integration.status === "live" ? (
            <Check className="h-3 w-3" />
          ) : null}
          {integration.status.charAt(0).toUpperCase() + integration.status.slice(1)}
        </span>
      </div>
      <div className="text-[11px] text-ink-500 mt-1">{integration.hint}</div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Security — change password + sign out + (placeholders) 2FA
// ---------------------------------------------------------------------------

function SecuritySection({ onLogout }: { onLogout: () => Promise<void> }) {
  const [openPwd, setOpenPwd] = useState(false);

  return (
    <SectionCard title="Security" subtitle="Account safety" className="lg:col-span-2">
      <ul className="space-y-2.5">
        <SettingRow
          Icon={KeyRound}
          label="Password"
          value={openPwd ? "" : "Change your login password"}
          action={
            !openPwd ? (
              <button
                onClick={() => setOpenPwd(true)}
                className="btn-ghost text-xs"
              >
                Change
                <ChevronRight className="h-3 w-3" />
              </button>
            ) : null
          }
        />
        {openPwd && <ChangePasswordForm onClose={() => setOpenPwd(false)} />}

        <SettingRow
          Icon={ShieldCheck}
          label="Two-factor auth (TOTP)"
          value="Not configured"
          muted
          action={
            <button
              className="btn-ghost text-xs opacity-50 cursor-not-allowed"
              disabled
              title="TOTP setup coming in Session 4"
            >
              Set up
              <ChevronRight className="h-3 w-3" />
            </button>
          }
        />

        <SettingRow
          Icon={LogOut}
          label="Sign out"
          value="End this session on this device"
          action={
            <button
              onClick={() => void onLogout()}
              className="btn bg-rose-50 text-rose-700 ring-1 ring-rose-200 hover:bg-rose-100"
            >
              Sign out
            </button>
          }
        />
      </ul>
    </SectionCard>
  );
}

function ChangePasswordForm({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = useCallback(async () => {
    setError(null);
    if (next.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    if (next !== confirm) {
      setError("New password and confirmation don't match.");
      return;
    }
    setBusy(true);
    try {
      await api.changePassword(current, next);
      setDone(true);
      setTimeout(() => onClose(), 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't change password.");
    } finally {
      setBusy(false);
    }
  }, [current, next, confirm, onClose]);

  if (done) {
    return (
      <div className="rounded-lg ring-1 ring-emerald-200 bg-emerald-50 text-emerald-900 p-3 text-sm flex items-center gap-2 ml-12">
        <Check className="h-4 w-4" />
        Password updated.
      </div>
    );
  }

  return (
    <div className="ml-12 rounded-lg ring-1 ring-ink-200 bg-white p-3 space-y-2">
      <PasswordField
        label="Current password"
        value={current}
        onChange={setCurrent}
        autoFocus
      />
      <PasswordField label="New password" value={next} onChange={setNext} />
      <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} />
      {error && (
        <div className="text-xs text-rose-700 flex items-start gap-1">
          <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
          {error}
        </div>
      )}
      <div className="flex items-center justify-end gap-2 pt-1">
        <button
          onClick={onClose}
          className="btn-ghost text-xs"
          disabled={busy}
        >
          Cancel
        </button>
        <button
          onClick={submit}
          disabled={busy || !current || !next || !confirm}
          className="btn-primary"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
          Update password
        </button>
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
        autoComplete={label.toLowerCase().includes("current") ? "current-password" : "new-password"}
        className="w-full rounded-lg ring-1 ring-ink-200 bg-white px-3 py-2 text-sm focus:ring-2 focus:ring-brand-300 focus:outline-none"
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// Shared row component
// ---------------------------------------------------------------------------

function SettingRow({
  Icon,
  label,
  value,
  badge,
  muted,
  action,
}: {
  Icon: typeof Building2;
  label: string;
  value: string;
  badge?: string;
  muted?: boolean;
  action?: React.ReactNode;
}) {
  return (
    <li className="flex items-center gap-3">
      <div className="h-9 w-9 rounded-lg bg-ink-100 text-ink-600 flex items-center justify-center shrink-0">
        <Icon className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] text-ink-500 uppercase tracking-wider">
          {label}
        </div>
        <div className={cn("text-sm truncate", muted ? "text-ink-400 italic" : "text-ink-900")}>
          {value}
          {badge && (
            <span className="ml-2 chip bg-amber-50 text-amber-700 align-middle">
              <Sparkles className="h-3 w-3" />
              {badge}
            </span>
          )}
        </div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </li>
  );
}
