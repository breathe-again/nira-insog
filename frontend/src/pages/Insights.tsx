/** Insights page — the full list, filterable, with mute / dismiss actions.
 *
 * The Dashboard widget only shows the top 4 most-severe live insights.
 * This page is where the founder goes to triage the rest.
 *
 * Filters:
 *   - severity:        all / info / attention / urgent
 *   - status:          live / dismissed / all
 *
 * Actions per row:
 *   - Dismiss (one-click, soft-delete via dismissed_at)
 *   - Mute vendor — silences future anomaly insights for that vendor
 *
 * Both write FeedbackEvent + AuditEvent rows server-side so the learning
 * layer picks up the signal.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Filter,
  Loader2,
  Sparkles,
  Trash2,
  VolumeX,
} from "lucide-react";
import TopBar from "../components/TopBar";
import { api } from "../api";
import type { InsightOut } from "../types";
import { cn } from "../lib/cn";

type SeverityFilter = "all" | "info" | "attention" | "urgent";
type StatusFilter = "live" | "dismissed" | "all";

const SEVERITY_META: Record<
  "info" | "attention" | "urgent",
  { Icon: typeof Sparkles; chip: string; ring: string }
> = {
  info: {
    Icon: Sparkles,
    chip: "bg-brand-50 text-brand-700",
    ring: "ring-brand-100",
  },
  attention: {
    Icon: AlertTriangle,
    chip: "bg-amber-50 text-amber-700",
    ring: "ring-amber-200",
  },
  urgent: {
    Icon: AlertTriangle,
    chip: "bg-rose-50 text-rose-700",
    ring: "ring-rose-200",
  },
};

export default function Insights() {
  const [items, setItems] = useState<InsightOut[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("live");

  const [actionId, setActionId] = useState<string | null>(null);

  const fetchInsights = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listInsights({
        severity: severity === "all" ? undefined : severity,
        include_dismissed: status !== "live",
        limit: 200,
      });
      // Backend doesn't filter by "dismissed-only", so we filter client-side
      // when the user picks that.
      const filtered =
        status === "dismissed"
          ? res.items.filter((i) => i.dismissed_at !== null)
          : status === "live"
            ? res.items.filter((i) => i.dismissed_at === null)
            : res.items;
      setItems(filtered);
      setTotal(filtered.length);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load insights");
    } finally {
      setLoading(false);
    }
  }, [severity, status]);

  useEffect(() => {
    void fetchInsights();
  }, [fetchInsights]);

  async function dismiss(id: string) {
    setActionId(id);
    try {
      await api.dismissInsight(id);
      await fetchInsights();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Dismiss failed");
    } finally {
      setActionId(null);
    }
  }

  async function muteVendor(id: string) {
    setActionId(id);
    try {
      await api.patchInsight(id, { mute_vendor: true });
      await fetchInsights();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Mute failed");
    } finally {
      setActionId(null);
    }
  }

  const counts = useMemo(() => {
    return {
      urgent: items.filter((i) => i.severity === "urgent" && !i.dismissed_at).length,
      attention: items.filter((i) => i.severity === "attention" && !i.dismissed_at).length,
      info: items.filter((i) => i.severity === "info" && !i.dismissed_at).length,
    };
  }, [items]);

  return (
    <>
      <TopBar
        title="Insights"
        subtitle={`${total} item${total === 1 ? "" : "s"} matching filters`}
      />

      <div className="p-6 space-y-6">
        {/* Summary chips */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <SummaryChip
            label="Urgent"
            value={counts.urgent}
            tone="rose"
            Icon={AlertTriangle}
          />
          <SummaryChip
            label="Attention"
            value={counts.attention}
            tone="amber"
            Icon={AlertTriangle}
          />
          <SummaryChip
            label="Informational"
            value={counts.info}
            tone="brand"
            Icon={Sparkles}
          />
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3 bg-white rounded-2xl ring-1 ring-ink-200 p-3">
          <div className="flex items-center gap-1.5 text-xs text-ink-500 px-2">
            <Filter className="h-3.5 w-3.5" />
            Filters
          </div>
          <FilterGroup
            value={severity}
            onChange={(v) => setSeverity(v as SeverityFilter)}
            options={[
              { label: "All severities", value: "all" },
              { label: "Urgent", value: "urgent" },
              { label: "Attention", value: "attention" },
              { label: "Info", value: "info" },
            ]}
          />
          <FilterGroup
            value={status}
            onChange={(v) => setStatus(v as StatusFilter)}
            options={[
              { label: "Live", value: "live" },
              { label: "Dismissed", value: "dismissed" },
              { label: "Both", value: "all" },
            ]}
          />
        </div>

        {/* Body */}
        {error && (
          <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 text-rose-700 px-4 py-3 text-sm">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-16 text-ink-400">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <div className="bg-white rounded-2xl ring-1 ring-ink-200 p-12 text-center">
            <CheckCircle2 className="h-8 w-8 text-emerald-500 mx-auto mb-3" />
            <div className="text-sm font-medium text-ink-900">
              No insights match these filters
            </div>
            <div className="text-xs text-ink-500 mt-1">
              {status === "live"
                ? "Your books look clean. Upload more documents and we'll keep watching."
                : "No dismissed insights yet."}
            </div>
          </div>
        ) : (
          <div className="grid gap-3">
            {items.map((insight) => (
              <InsightCard
                key={insight.id}
                insight={insight}
                busy={actionId === insight.id}
                onDismiss={() => dismiss(insight.id)}
                onMuteVendor={() => muteVendor(insight.id)}
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface InsightCardProps {
  insight: InsightOut;
  busy: boolean;
  onDismiss: () => void;
  onMuteVendor: () => void;
}

function InsightCard({ insight, busy, onDismiss, onMuteVendor }: InsightCardProps) {
  const meta =
    SEVERITY_META[(insight.severity as "info" | "attention" | "urgent") ?? "info"] ??
    SEVERITY_META.info;

  const isDismissed = insight.dismissed_at !== null;
  const technical =
    insight.supporting_data && typeof insight.supporting_data === "object"
      ? (insight.supporting_data as Record<string, unknown>).technical
      : null;
  const vendorId =
    insight.supporting_data && typeof insight.supporting_data === "object"
      ? (insight.supporting_data as Record<string, unknown>).vendor_id
      : null;

  return (
    <div
      className={cn(
        "bg-white rounded-2xl ring-1 p-4 flex gap-3",
        meta.ring,
        isDismissed && "opacity-60",
      )}
    >
      <div
        className={cn(
          "h-9 w-9 rounded-xl flex items-center justify-center shrink-0",
          meta.chip,
        )}
      >
        <meta.Icon className="h-4 w-4" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="text-sm font-semibold text-ink-900">{insight.title}</h3>
          <span
            className={cn(
              "text-[10px] uppercase tracking-wider rounded-full px-1.5 py-0.5",
              meta.chip,
            )}
          >
            {insight.severity}
          </span>
          {isDismissed && (
            <span className="text-[10px] uppercase tracking-wider rounded-full px-1.5 py-0.5 bg-ink-100 text-ink-500">
              Dismissed
            </span>
          )}
        </div>
        <p className="text-sm text-ink-700 mt-1.5 leading-relaxed">{insight.body}</p>
        {typeof technical === "string" && (
          <details className="mt-2">
            <summary className="text-[11px] text-ink-500 cursor-pointer hover:text-ink-700 select-none">
              Why this insight?
            </summary>
            <div className="text-[11px] text-ink-500 mt-1 font-mono">{technical}</div>
          </details>
        )}
      </div>

      {!isDismissed && (
        <div className="flex flex-col gap-1.5 shrink-0">
          {vendorId && (
            <button
              type="button"
              onClick={onMuteVendor}
              disabled={busy}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-ink-700 hover:bg-ink-100 disabled:opacity-50"
              title="Stop flagging this vendor's payments"
            >
              <VolumeX className="h-3.5 w-3.5" />
              Mute vendor
            </button>
          )}
          <button
            type="button"
            onClick={onDismiss}
            disabled={busy}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-ink-700 hover:bg-ink-100 disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

interface SummaryChipProps {
  label: string;
  value: number;
  tone: "rose" | "amber" | "brand";
  Icon: typeof Sparkles;
}

function SummaryChip({ label, value, tone, Icon }: SummaryChipProps) {
  const toneClass = {
    rose: "from-rose-50 to-white ring-rose-200 text-rose-700",
    amber: "from-amber-50 to-white ring-amber-200 text-amber-700",
    brand: "from-brand-50 to-white ring-brand-200 text-brand-700",
  }[tone];

  return (
    <div
      className={cn(
        "rounded-2xl ring-1 p-4 bg-gradient-to-br flex items-center gap-3",
        toneClass,
      )}
    >
      <Icon className="h-5 w-5" />
      <div>
        <div className="text-xs uppercase tracking-wider opacity-80">{label}</div>
        <div className="text-2xl font-semibold text-ink-900 tabular">{value}</div>
      </div>
    </div>
  );
}

interface FilterGroupProps {
  value: string;
  onChange: (v: string) => void;
  options: { label: string; value: string }[];
}

function FilterGroup({ value, onChange, options }: FilterGroupProps) {
  return (
    <div className="flex items-center gap-1 bg-ink-50 rounded-lg p-0.5">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            "px-2.5 py-1 rounded-md text-xs font-medium transition-colors",
            opt.value === value
              ? "bg-white text-ink-900 shadow-sm ring-1 ring-ink-200"
              : "text-ink-600 hover:text-ink-900",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

