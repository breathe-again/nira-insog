/** Settings page.
 *
 * Four real sections, all backed by live data:
 *   1. Workspace — org name + plan from /api/auth/me
 *   2. Team     — current user; future "Invite" once the endpoint lands
 *   3. Integrations — actual status of each connector we've shipped
 *   4. Security — change password, sign out, future 2FA / sessions
 */

import { useCallback, useState } from "react";
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronRight,
  KeyRound,
  Loader2,
  LogOut,
  Mail,
  ShieldCheck,
  Sparkles,
  UserPlus,
  Users,
} from "lucide-react";
import TopBar from "../components/TopBar";
import SectionCard from "../components/SectionCard";
import { api } from "../api";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "../lib/cn";

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
  const { user, logout } = useAuth();

  return (
    <>
      <TopBar
        title="Settings"
        subtitle="Workspace · team · integrations · security"
      />

      <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <WorkspaceSection user={user} />
        <TeamSection user={user} />
        <IntegrationsSection />
        <SecuritySection onLogout={logout} />
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Workspace
// ---------------------------------------------------------------------------

function WorkspaceSection({ user }: { user: ReturnType<typeof useAuth>["user"] }) {
  return (
    <SectionCard title="Workspace" subtitle="Organization details">
      <ul className="space-y-3">
        <SettingRow
          Icon={Building2}
          label="Organization name"
          value={user?.org_name ?? "—"}
        />
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
  const [showInvite, setShowInvite] = useState(false);

  return (
    <SectionCard title="Team" subtitle="Members and roles">
      <ul className="space-y-3">
        {user && (
          <li className="flex items-center gap-3 rounded-lg ring-1 ring-emerald-100 bg-emerald-50/40 p-3">
            <div className="h-9 w-9 rounded-lg bg-emerald-100 text-emerald-700 flex items-center justify-center">
              <Users className="h-4 w-4" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-ink-900 truncate">
                {user.email}
              </div>
              <div className="text-[11px] text-ink-500 mt-0.5 uppercase tracking-wider">
                {user.role}
              </div>
            </div>
            <span className="chip bg-emerald-100 text-emerald-700">You</span>
          </li>
        )}
      </ul>
      <button
        className="btn bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50 mt-3 w-full justify-center"
        onClick={() => setShowInvite(true)}
        disabled
        title="Multi-user invites coming soon"
      >
        <UserPlus className="h-3.5 w-3.5" />
        Invite teammate
      </button>
      <div className="text-[11px] text-ink-500 mt-2">
        Multi-user invites are queued for Session 4. For now every org is a
        single-founder workspace.
      </div>
      {showInvite && (
        <div className="mt-3 rounded-lg ring-1 ring-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          Invites endpoint isn't live yet. Ping us and we'll add the second
          seat manually for now.
          <button
            className="ml-2 underline"
            onClick={() => setShowInvite(false)}
          >
            dismiss
          </button>
        </div>
      )}
    </SectionCard>
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
