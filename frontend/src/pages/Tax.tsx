/** Tax intelligence page.
 *
 * Three widgets stacked vertically:
 *  1. GSTIN compliance — every vendor/client + their GSTIN status
 *  2. Advance tax — quarterly installment timeline
 *  3. TDS draft — vendors over threshold + suggested deductions
 *
 * Everything is read-only and recomputed on each load (no caching) so the
 * numbers stay fresh as new documents land.
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  Download,
  FileText,
  Info,
  Loader2,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import TopBar from "../components/TopBar";
import SectionCard from "../components/SectionCard";
import StatCard from "../components/StatCard";
import EmptyState from "../components/EmptyState";
import { api } from "../api";
import type {
  AdvanceTaxOut,
  CounterpartyGSTINOut,
  GSTINHealthOut,
  TDSDraftOut,
  TaxInstallmentOut,
  VendorTDSRowOut,
} from "../types";
import { formatINR, formatINRShort } from "../lib/format";
import { cn } from "../lib/cn";

type EntityType = "company" | "individual" | "professional" | "llp";

export default function Tax() {
  const [gstin, setGstin] = useState<GSTINHealthOut | null>(null);
  const [advance, setAdvance] = useState<AdvanceTaxOut | null>(null);
  const [tds, setTds] = useState<TDSDraftOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [entityType, setEntityType] = useState<EntityType>(() => {
    try {
      return (
        (window.localStorage.getItem("nira:tax:entity") as EntityType) ?? "company"
      );
    } catch {
      return "company";
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem("nira:tax:entity", entityType);
    } catch {
      // ignore
    }
  }, [entityType]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [g, a, t] = await Promise.all([
        api.gstinHealth(),
        api.advanceTax(entityType),
        api.tdsDraft(),
      ]);
      setGstin(g);
      setAdvance(a);
      setTds(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [entityType]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <TopBar
        title="Tax"
        subtitle="GSTIN compliance · Advance tax · TDS draft"
        actions={
          <div className="flex items-center gap-2">
            <label className="text-xs text-ink-600">Entity type</label>
            <select
              value={entityType}
              onChange={(e) => setEntityType(e.target.value as EntityType)}
              className="text-xs rounded-lg ring-1 ring-ink-200 bg-white px-2 py-1"
            >
              <option value="company">Company (25% + cess)</option>
              <option value="llp">LLP (30% + cess)</option>
              <option value="individual">Individual / Professional</option>
            </select>
          </div>
        }
      />

      <div className="p-6 space-y-6">
        {error && (
          <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 text-rose-900 p-4 text-sm">
            {error}
          </div>
        )}

        {loading && !gstin && (
          <div className="rounded-xl bg-ink-50 ring-1 ring-ink-200 text-ink-700 p-4 text-sm flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            Computing tax position…
          </div>
        )}

        {gstin && <GSTINHealthSection data={gstin} />}
        {advance && <AdvanceTaxSection data={advance} />}
        {tds && <TDSSection data={tds} />}

        <div className="rounded-xl bg-ink-50 ring-1 ring-ink-200 p-4 text-xs text-ink-600 flex items-start gap-2">
          <Info className="h-4 w-4 shrink-0 mt-0.5" />
          <div>
            These are <b>estimates</b> derived from your bank flows and uploaded
            invoices. Surcharge, cess composition, depreciation, MAT, ITC
            adjustments, and presumptive schemes can shift the real numbers
            significantly — always confirm with your CA before filing.
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// GSTIN health
// ---------------------------------------------------------------------------

function GSTINHealthSection({ data }: { data: GSTINHealthOut }) {
  const [filter, setFilter] = useState<"all" | "invalid" | "missing">("all");
  const filtered = data.counterparties.filter((c) => {
    if (filter === "invalid") return !c.is_valid && c.reason !== "missing";
    if (filter === "missing") return c.reason === "missing";
    return true;
  });

  return (
    <SectionCard
      title="GSTIN health"
      subtitle={`${data.total} counterparties · ${data.compliance_pct.toFixed(1)}% compliant`}
      action={
        <div className="flex items-center gap-1 bg-ink-50 rounded-lg p-0.5">
          {(["all", "invalid", "missing"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                "px-2 py-0.5 rounded-md text-[11px] font-medium capitalize",
                filter === f
                  ? "bg-white text-ink-900 shadow-sm ring-1 ring-ink-200"
                  : "text-ink-600 hover:text-ink-900",
              )}
            >
              {f}
            </button>
          ))}
        </div>
      }
    >
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <MiniTile label="Valid" value={data.valid} tone="ok" />
        <MiniTile label="Invalid" value={data.invalid} tone="warn" />
        <MiniTile label="Missing" value={data.missing} tone="info" />
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          Icon={CheckCircle2}
          title={filter === "all" ? "No counterparties yet" : "Nothing in this bucket"}
          description={
            filter === "all"
              ? "Upload an invoice or bank statement to populate vendors and clients."
              : "Switch the filter to see other counterparties."
          }
        />
      ) : (
        <ul className="divide-y divide-ink-100 -mx-1">
          {filtered.slice(0, 25).map((c) => (
            <CounterpartyRow key={`${c.role}:${c.id}`} c={c} />
          ))}
          {filtered.length > 25 && (
            <li className="px-3 py-2 text-xs text-ink-500">
              Showing 25 of {filtered.length} — narrow the filter to see more.
            </li>
          )}
        </ul>
      )}
    </SectionCard>
  );
}

function CounterpartyRow({ c }: { c: CounterpartyGSTINOut }) {
  const Icon = c.is_valid
    ? CheckCircle2
    : c.reason === "missing"
      ? Info
      : XCircle;
  const iconColor = c.is_valid
    ? "text-emerald-600"
    : c.reason === "missing"
      ? "text-ink-400"
      : "text-rose-600";

  return (
    <li className="py-2 px-3 flex items-center gap-3">
      <Icon className={cn("h-4 w-4 shrink-0", iconColor)} />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-ink-900 truncate">
          {c.name}{" "}
          <span className="text-[10px] uppercase tracking-wider text-ink-400 ml-1">
            {c.role}
          </span>
        </div>
        <div className="text-[11px] text-ink-500 mt-0.5 truncate">
          {c.gstin_raw ? (
            <>
              <span className="font-mono">{c.gstin_raw}</span>
              {c.state_name && <span className="ml-2">· {c.state_name}</span>}
              {c.pan && <span className="ml-2">· PAN {c.pan}</span>}
            </>
          ) : (
            <span className="italic text-ink-400">No GSTIN on file</span>
          )}
          {!c.is_valid && c.reason !== "missing" && c.reason && (
            <span className="ml-2 text-rose-600">— {c.reason}</span>
          )}
        </div>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Advance tax
// ---------------------------------------------------------------------------

function AdvanceTaxSection({ data }: { data: AdvanceTaxOut }) {
  const profitYTD = Number(data.net_profit_ytd);
  const projected = Number(data.projected_annual_profit);
  const annualTax = Number(data.estimated_annual_tax);

  return (
    <SectionCard
      title={`Advance tax — FY ${data.fy_label}`}
      subtitle={`${data.days_elapsed} days into FY · ${data.days_remaining} left`}
    >
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <MiniTile
          label="Net profit YTD"
          value={formatINRShort(profitYTD)}
          tone={profitYTD >= 0 ? "ok" : "warn"}
        />
        <MiniTile
          label="Projected annual"
          value={formatINRShort(projected)}
          tone="info"
        />
        <MiniTile
          label={`Estimated tax (${(data.estimated_tax_rate * 100).toFixed(1)}%)`}
          value={formatINRShort(annualTax)}
          tone="warn"
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        {data.installments.map((i) => (
          <InstallmentCard key={i.label} i={i} />
        ))}
      </div>

      {Number(data.total_overdue) > 0 && (
        <div className="mt-4 rounded-xl bg-rose-50 ring-1 ring-rose-200 text-rose-900 p-3 text-sm flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <div>
            <b>{formatINR(Number(data.total_overdue))}</b> in advance tax is past
            its installment date. Interest under section 234B/234C accrues on
            unpaid amounts.
          </div>
        </div>
      )}
    </SectionCard>
  );
}

function InstallmentCard({ i }: { i: TaxInstallmentOut }) {
  const tone = {
    overdue: "border-rose-300 bg-rose-50",
    due_soon: "border-amber-300 bg-amber-50",
    upcoming: "border-ink-200 bg-white",
    complete: "border-emerald-300 bg-emerald-50",
  }[i.status];
  const badge = {
    overdue: { text: `${Math.abs(i.days_until_due)}d overdue`, tone: "text-rose-700" },
    due_soon: { text: `Due in ${i.days_until_due}d`, tone: "text-amber-700" },
    upcoming: { text: `In ${i.days_until_due}d`, tone: "text-ink-600" },
    complete: { text: "Done", tone: "text-emerald-700" },
  }[i.status];

  return (
    <div className={cn("rounded-xl border p-3", tone)}>
      <div className="flex items-center justify-between">
        <div className="text-xs font-semibold text-ink-700">{i.label}</div>
        <span className={cn("text-[10px] uppercase tracking-wider", badge.tone)}>
          {badge.text}
        </span>
      </div>
      <div className="mt-1 text-base font-semibold tabular-nums">
        {formatINRShort(Number(i.this_installment))}
      </div>
      <div className="text-[11px] text-ink-500">
        Due {new Date(i.due_date).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
      </div>
      <div className="text-[10px] text-ink-400 mt-1">
        Cumulative {(i.cumulative_pct * 100).toFixed(0)}% · {formatINRShort(Number(i.cumulative_amount))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TDS draft
// ---------------------------------------------------------------------------

function TDSSection({ data }: { data: TDSDraftOut }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll
    ? data.rows
    : data.rows.filter((r) => r.has_crossed_threshold);

  return (
    <SectionCard
      title={`TDS draft — FY ${data.fy_label}`}
      subtitle={`${data.vendors_crossed_threshold} of ${data.total_vendors} vendors over threshold · estimated ₹${formatINRShort(Number(data.total_tds_estimated))} TDS`}
      action={
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAll(!showAll)}
            className="btn bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50"
          >
            {showAll ? "Above threshold only" : "Show all vendors"}
          </button>
          <button
            onClick={() => downloadTDSCSV(data)}
            className="btn bg-brand-50 text-brand-700 ring-1 ring-brand-200 hover:bg-brand-100"
          >
            <Download className="h-3.5 w-3.5" />
            CSV
          </button>
        </div>
      }
    >
      {visible.length === 0 ? (
        <EmptyState
          Icon={ShieldCheck}
          title={showAll ? "No vendors yet" : "No vendors past TDS threshold"}
          description={
            showAll
              ? "Vendors appear once they receive a payment from your bank account."
              : `Click "Show all vendors" to see ${data.total_vendors} vendors still below threshold.`
          }
        />
      ) : (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-ink-200 text-left text-[10px] uppercase tracking-wider text-ink-500">
                <th className="py-2 px-2">Vendor</th>
                <th className="py-2 px-2">Section</th>
                <th className="py-2 px-2 text-right">FY paid</th>
                <th className="py-2 px-2 text-right">Threshold</th>
                <th className="py-2 px-2 text-right">Rate</th>
                <th className="py-2 px-2 text-right">TDS</th>
                <th className="py-2 px-2">Form</th>
              </tr>
            </thead>
            <tbody>
              {visible.slice(0, 50).map((r) => (
                <TDSRow key={r.vendor_id} r={r} />
              ))}
            </tbody>
          </table>
          {visible.length > 50 && (
            <div className="px-3 py-2 text-xs text-ink-500">
              Showing 50 of {visible.length}. Download CSV for the full set.
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function TDSRow({ r }: { r: VendorTDSRowOut }) {
  return (
    <tr
      className={cn(
        "border-b border-ink-100",
        r.has_crossed_threshold ? "bg-rose-50/40" : "",
      )}
    >
      <td className="py-2 px-2">
        <div className="font-medium text-ink-900">{r.vendor_name}</div>
        <div className="text-[10px] text-ink-500">
          {r.pan ? `PAN ${r.pan}` : "PAN missing"}
        </div>
      </td>
      <td className="py-2 px-2">
        <span className="font-mono text-ink-800">{r.section_code}</span>
        <div className="text-[10px] text-ink-500">{r.section_label}</div>
      </td>
      <td className="py-2 px-2 text-right tabular-nums">
        {formatINR(Number(r.fy_payments_total))}
      </td>
      <td className="py-2 px-2 text-right tabular-nums text-ink-500">
        {formatINR(Number(r.threshold))}
      </td>
      <td className="py-2 px-2 text-right tabular-nums">
        {(r.applicable_rate * 100).toFixed(2)}%
      </td>
      <td
        className={cn(
          "py-2 px-2 text-right tabular-nums font-semibold",
          r.has_crossed_threshold ? "text-rose-700" : "text-ink-400",
        )}
      >
        {r.has_crossed_threshold ? formatINR(Number(r.tds_amount_estimated)) : "—"}
      </td>
      <td className="py-2 px-2">
        <span className="chip bg-violet-50 text-violet-700">{r.form_quarterly}</span>
      </td>
    </tr>
  );
}

function downloadTDSCSV(data: TDSDraftOut) {
  const headers = [
    "Vendor",
    "PAN",
    "Section",
    "Section label",
    "FY paid (INR)",
    "Threshold (INR)",
    "Rate (%)",
    "TDS estimated (INR)",
    "Net payable (INR)",
    "Form",
    "Status",
    "Notes",
  ];
  const rows = data.rows.map((r) => [
    r.vendor_name,
    r.pan ?? "",
    r.section_code,
    r.section_label,
    r.fy_payments_total,
    r.threshold,
    (r.applicable_rate * 100).toFixed(2),
    r.tds_amount_estimated,
    r.net_payable_after_tds,
    r.form_quarterly,
    r.deduction_status,
    r.notes ?? "",
  ]);
  const csv = [headers, ...rows]
    .map((row) =>
      row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","),
    )
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `tds-draft-fy${data.fy_label}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function MiniTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone: "ok" | "warn" | "info";
}) {
  const palette = {
    ok: "bg-emerald-50 text-emerald-900 ring-emerald-200",
    warn: "bg-amber-50 text-amber-900 ring-amber-200",
    info: "bg-brand-50 text-brand-900 ring-brand-200",
  }[tone];
  return (
    <div className={cn("rounded-xl ring-1 p-3", palette)}>
      <div className="text-[11px] uppercase tracking-wider opacity-80">{label}</div>
      <div className="text-xl font-semibold tabular-nums mt-1">{value}</div>
    </div>
  );
}
