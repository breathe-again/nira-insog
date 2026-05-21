import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Banknote,
  Coins,
  HandCoins,
  LineChart,
  ShoppingCart,
  Sparkles,
  Wallet,
} from "lucide-react";
import TopBar from "../components/TopBar";
import StatCard from "../components/StatCard";
import SectionCard from "../components/SectionCard";
import InsightCard from "../components/InsightCard";
import CashFlowChart from "../components/charts/CashFlowChart";
import ExpenseDonut from "../components/charts/ExpenseDonut";
import ForecastChart from "../components/charts/ForecastChart";
import { api } from "../api";
import { demo } from "../data/demoData";
import type { DashboardSummaryOut } from "../types";
import { deltaLabel, formatINR, formatINRShort, timeAgo } from "../lib/format";
import { cn } from "../lib/cn";

// ---------------------------------------------------------------------------
// Unified view-model — both demo and real responses adapt to this shape so the
// JSX below doesn't have to branch per-widget.
// ---------------------------------------------------------------------------

interface ViewModel {
  cashPosition: number;
  cashPositionPrev: number;
  receivablesTotal: number;
  receivablesPrev: number;
  payablesTotal: number;
  payablesPrev: number;
  netFlowMtd: number;
  netFlowMtdPrev: number;
  cashFlow: { date: string; in: number; out: number; net: number }[];
  receivablesAging: { bucket: string; amount: number }[];
  expenseByCategory: { name: string; value: number; color: string }[];
  topVendors: { name: string; amount: number; deltaPct: number }[];
  topClients: { name: string; amount: number; deltaPct: number }[];
  insights: {
    id: string;
    severity: "info" | "attention" | "urgent";
    title: string;
    body: string;
    time: string;
  }[];
  forecast: { date: string; forecast: number; lowerBand: number; upperBand: number }[];
  compliance: { status: "ok" | "warn" | "fail"; label: string }[];
  hasAnyData: boolean;
  isLive: boolean;        // true when from real API
  bankTxnCount: number;
}

function adaptDemo(): ViewModel {
  return {
    cashPosition: demo.cashPosition,
    cashPositionPrev: demo.cashPositionPrev,
    receivablesTotal: demo.receivablesTotal,
    receivablesPrev: demo.receivablesTotal * 0.92,
    payablesTotal: demo.payablesTotal,
    payablesPrev: demo.payablesPrev,
    netFlowMtd: demo.monthRevenue - demo.monthExpense,
    netFlowMtdPrev: (demo.monthRevenue - demo.monthExpense) / 1.14,
    cashFlow: demo.cashFlow,
    receivablesAging: demo.receivablesAging,
    expenseByCategory: demo.expenseByCategory,
    topVendors: demo.topVendors,
    topClients: demo.topClients,
    insights: demo.insights,
    forecast: demo.forecastSeries,
    compliance: [
      { status: "ok", label: "100% of sales invoices have GSTIN" },
      { status: "ok", label: "98% of purchases have HSN codes" },
      { status: "warn", label: "7 receipts missing vendor" },
      { status: "ok", label: "Bank statements complete through May 18" },
    ],
    hasAnyData: true,
    isLive: false,
    bankTxnCount: 0,
  };
}

function adaptSummary(s: DashboardSummaryOut): ViewModel {
  return {
    cashPosition: Number(s.cash_position.value),
    cashPositionPrev: Number(s.cash_position.prev_value),
    receivablesTotal: Number(s.receivables.value),
    receivablesPrev: Number(s.receivables.prev_value),
    payablesTotal: Number(s.payables.value),
    payablesPrev: Number(s.payables.prev_value),
    netFlowMtd: Number(s.net_flow_mtd.value),
    netFlowMtdPrev: Number(s.net_flow_mtd.prev_value),
    cashFlow: s.cash_flow.map((p) => ({
      date: p.date,
      in: Number(p.in_amount),
      out: Number(p.out_amount),
      net: Number(p.net),
    })),
    receivablesAging: s.receivables_aging.map((b) => ({
      bucket: b.bucket,
      amount: Number(b.amount),
    })),
    expenseByCategory: s.expense_breakdown.map((c) => ({
      name: c.name,
      value: Number(c.value),
      color: c.color,
    })),
    topVendors: s.top_vendors.map((v) => ({
      name: v.name,
      amount: Number(v.amount),
      deltaPct: Number(v.delta_pct),
    })),
    topClients: s.top_clients.map((c) => ({
      name: c.name,
      amount: Number(c.amount),
      deltaPct: Number(c.delta_pct),
    })),
    insights: s.insights.map((i) => ({
      id: i.id,
      severity: i.severity,
      title: i.title,
      body: i.body,
      time: timeAgo(i.created_at),
    })),
    forecast: s.forecast.map((p) => ({
      date: p.date,
      forecast: Number(p.forecast),
      lowerBand: Number(p.lower_band),
      upperBand: Number(p.upper_band),
    })),
    compliance: s.compliance.map((c) => ({ status: c.status, label: c.label })),
    hasAnyData: s.has_any_data,
    isLive: true,
    bankTxnCount: s.bank_txn_count,
  };
}

// ---------------------------------------------------------------------------
// Dashboard component
// ---------------------------------------------------------------------------

export default function Dashboard() {
  const [demoMode, setDemoMode] = useState(true);
  const [summary, setSummary] = useState<DashboardSummaryOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch real data whenever demo is off; refetch when toggled on→off.
  useEffect(() => {
    if (demoMode) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .dashboardSummary()
      .then((s) => {
        if (!cancelled) setSummary(s);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [demoMode]);

  const vm: ViewModel = useMemo(() => {
    if (demoMode) return adaptDemo();
    if (summary) return adaptSummary(summary);
    // No data yet (still loading / errored). Provide a neutral empty VM so the
    // layout doesn't collapse; widgets check `hasAnyData` for their own UX.
    return {
      cashPosition: 0,
      cashPositionPrev: 0,
      receivablesTotal: 0,
      receivablesPrev: 0,
      payablesTotal: 0,
      payablesPrev: 0,
      netFlowMtd: 0,
      netFlowMtdPrev: 0,
      cashFlow: [],
      receivablesAging: [],
      expenseByCategory: [],
      topVendors: [],
      topClients: [],
      insights: [],
      forecast: [],
      compliance: [],
      hasAnyData: false,
      isLive: true,
      bankTxnCount: 0,
    };
  }, [demoMode, summary]);

  const cashDelta = deltaLabel(vm.cashPosition, vm.cashPositionPrev);
  const recvDelta = deltaLabel(vm.receivablesTotal, vm.receivablesPrev);
  const payablesDelta = deltaLabel(vm.payablesTotal, vm.payablesPrev);
  const netFlowDelta = deltaLabel(vm.netFlowMtd, vm.netFlowMtdPrev);

  const showRealEmptyBanner = !demoMode && !loading && !error && summary && !vm.hasAnyData;

  return (
    <>
      <TopBar
        title="Dashboard"
        subtitle="Real-time financial picture · Demo Org"
        actions={
          <button
            onClick={() => setDemoMode(!demoMode)}
            className={cn(
              "btn ring-1 transition-colors",
              demoMode
                ? "bg-brand-50 text-brand-700 ring-brand-200 hover:bg-brand-100"
                : "bg-white text-ink-700 ring-ink-200 hover:bg-ink-50",
            )}
          >
            <Sparkles className="h-3.5 w-3.5" />
            {demoMode ? "Demo data on" : "Demo data off"}
          </button>
        }
      />

      <div className="p-6 space-y-6">
        {/* Status banner — only when demo is off */}
        {!demoMode && (
          <>
            {loading && (
              <div className="rounded-xl bg-ink-50 ring-1 ring-ink-200 text-ink-700 p-4 text-sm flex items-center gap-2">
                <Sparkles className="h-4 w-4 shrink-0 animate-pulse" />
                Loading your real numbers…
              </div>
            )}
            {error && (
              <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 text-rose-900 p-4 text-sm">
                Couldn't load dashboard: {error}
              </div>
            )}
            {showRealEmptyBanner && (
              <div className="rounded-xl bg-amber-50 ring-1 ring-amber-200 text-amber-900 p-4 text-sm flex items-start gap-2">
                <Sparkles className="h-4 w-4 mt-0.5 shrink-0" />
                <div>
                  No bank transactions yet. Upload statements in the{" "}
                  <a href="/inbox" className="font-medium underline">
                    Inbox
                  </a>{" "}
                  to populate these widgets.
                </div>
              </div>
            )}
            {vm.isLive && vm.hasAnyData && (
              <div className="rounded-xl bg-emerald-50 ring-1 ring-emerald-200 text-emerald-900 p-3 text-xs flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                Live data — {vm.bankTxnCount.toLocaleString()} bank transactions processed.
              </div>
            )}
          </>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 animate-slide-up">
          <StatCard
            label="Cash position"
            value={vm.hasAnyData ? formatINRShort(vm.cashPosition) : "—"}
            hint="across all accounts"
            delta={vm.hasAnyData ? cashDelta : undefined}
            goodWhen="up"
            Icon={Wallet}
            accent="indigo"
          />
          <StatCard
            label="Receivables"
            value={vm.hasAnyData ? formatINRShort(vm.receivablesTotal) : "—"}
            hint="outstanding"
            delta={vm.hasAnyData ? recvDelta : undefined}
            goodWhen="down"
            Icon={HandCoins}
            accent="violet"
          />
          <StatCard
            label="Payables"
            value={vm.hasAnyData ? formatINRShort(vm.payablesTotal) : "—"}
            hint="due this month"
            delta={vm.hasAnyData ? payablesDelta : undefined}
            goodWhen="down"
            Icon={ShoppingCart}
            accent="amber"
          />
          <StatCard
            label="Net flow (MTD)"
            value={vm.hasAnyData ? formatINRShort(vm.netFlowMtd) : "—"}
            hint="month-to-date"
            delta={vm.hasAnyData ? netFlowDelta : undefined}
            goodWhen="up"
            Icon={Banknote}
            accent="emerald"
          />
        </div>

        {/* Cash flow + Expense donut */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <SectionCard
            title="Cash flow"
            subtitle="Last 30 days · inflow vs outflow"
            className="xl:col-span-2"
            action={
              <button className="btn-ghost text-xs">
                Last 30 days
                <ArrowRight className="h-3 w-3" />
              </button>
            }
          >
            {vm.cashFlow.length > 0 ? (
              <CashFlowChart data={vm.cashFlow} />
            ) : (
              <EmptyChart label="Upload a bank statement to see cash flow." />
            )}
          </SectionCard>

          <SectionCard title="Expense breakdown" subtitle="This month, by category">
            {vm.expenseByCategory.length > 0 ? (
              <ExpenseDonut data={vm.expenseByCategory} />
            ) : (
              <EmptyChart label="No expenses categorized yet." />
            )}
          </SectionCard>
        </div>

        {/* Receivables aging + Insights */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <SectionCard
            title="Receivables aging"
            subtitle="Outstanding invoices by bucket"
            className="xl:col-span-2"
          >
            {vm.receivablesAging.reduce((s, b) => s + b.amount, 0) > 0 ? (
              <ReceivablesAging buckets={vm.receivablesAging} />
            ) : (
              <EmptyChart label="No outstanding invoices." />
            )}
          </SectionCard>

          <SectionCard
            title="Insights"
            subtitle={vm.insights.length ? "Live feed · top 4" : "Will populate as data lands"}
            action={
              vm.insights.length > 0 ? (
                <button className="btn-ghost text-xs">
                  See all
                  <ArrowRight className="h-3 w-3" />
                </button>
              ) : null
            }
          >
            <div className="space-y-2.5">
              {vm.insights.length > 0 ? (
                vm.insights.map((i) => (
                  <InsightCard
                    key={i.id}
                    severity={i.severity}
                    title={i.title}
                    body={i.body}
                    time={i.time}
                  />
                ))
              ) : (
                <EmptyChart label="Insights appear once documents are processed." />
              )}
            </div>
          </SectionCard>
        </div>

        {/* Forecast + top vendors */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <SectionCard
            title="Cash forecast"
            subtitle="Next 30 days · with confidence band"
            className="xl:col-span-2"
            action={
              <span className="chip bg-brand-50 text-brand-700">
                <LineChart className="h-3 w-3" />
                {vm.isLive ? "Linear model" : "Prophet model"}
              </span>
            }
          >
            {vm.forecast.length > 0 && vm.hasAnyData ? (
              <ForecastChart data={vm.forecast} />
            ) : (
              <EmptyChart label="Needs 30+ days of transactions to forecast." />
            )}
          </SectionCard>

          <SectionCard title="Top vendors" subtitle="By spend this month">
            {vm.topVendors.length > 0 ? (
              <ul className="divide-y divide-ink-100 -mx-1">
                {vm.topVendors.map((v) => (
                  <CounterpartyRow key={v.name} {...v} goodWhen="down" />
                ))}
              </ul>
            ) : (
              <EmptyChart label="No vendor data yet." />
            )}
          </SectionCard>
        </div>

        {/* Top clients + Compliance */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <SectionCard
            title="Top clients"
            subtitle="By revenue this month"
            className="xl:col-span-2"
          >
            {vm.topClients.length > 0 ? (
              <ul className="divide-y divide-ink-100">
                {vm.topClients.map((c) => (
                  <CounterpartyRow key={c.name} {...c} goodWhen="up" />
                ))}
              </ul>
            ) : (
              <EmptyChart label="No client data yet." />
            )}
          </SectionCard>

          <SectionCard title="Compliance" subtitle="GST · readiness check">
            {vm.compliance.length > 0 ? (
              <ul className="space-y-2.5">
                {vm.compliance.map((c, i) => (
                  <ComplianceRow key={i} status={c.status} label={c.label} />
                ))}
              </ul>
            ) : (
              <EmptyChart label="Checks appear once invoices are present." />
            )}
            {demoMode && (
              <div className="mt-4 rounded-lg bg-ink-50 p-3 text-xs text-ink-600">
                <span className="font-medium text-ink-800">Projected Q1 GST payable: </span>
                {formatINR(980000)} ± {formatINR(60000)}.
              </div>
            )}
          </SectionCard>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function ReceivablesAging({
  buckets,
}: {
  buckets: { bucket: string; amount: number }[];
}) {
  const total = buckets.reduce((s, b) => s + b.amount, 0);
  if (total <= 0) {
    return <EmptyChart label="No outstanding invoices." />;
  }
  return (
    <div className="space-y-3">
      <div className="text-xs text-ink-500">
        Total outstanding:{" "}
        <span className="font-semibold text-ink-900 tabular">{formatINR(total)}</span>
      </div>
      <div className="flex h-10 rounded-lg overflow-hidden ring-1 ring-ink-200">
        {buckets.map((b, i) => {
          const colors = ["bg-emerald-300", "bg-amber-300", "bg-orange-400", "bg-rose-500"];
          const pct = (b.amount / total) * 100;
          return (
            <div
              key={b.bucket}
              className={cn(
                colors[i] ?? "bg-ink-200",
                "flex items-center justify-center text-[11px] font-medium text-white",
              )}
              style={{ width: `${pct}%` }}
              title={`${b.bucket}: ${formatINR(b.amount)}`}
            >
              {pct > 12 ? `${pct.toFixed(0)}%` : ""}
            </div>
          );
        })}
      </div>
      <div className="grid grid-cols-4 gap-2 mt-2">
        {buckets.map((b, i) => {
          const colors = [
            "text-emerald-700",
            "text-amber-700",
            "text-orange-700",
            "text-rose-700",
          ];
          return (
            <div key={b.bucket} className="text-xs">
              <div className={cn("font-semibold", colors[i] ?? "text-ink-700")}>
                {b.bucket} days
              </div>
              <div className="text-ink-900 tabular text-sm font-medium">
                {formatINRShort(b.amount)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CounterpartyRow({
  name,
  amount,
  deltaPct,
  goodWhen,
}: {
  name: string;
  amount: number;
  deltaPct: number;
  goodWhen: "up" | "down";
}) {
  const dir = deltaPct > 0.5 ? "up" : deltaPct < -0.5 ? "down" : "flat";
  const good =
    dir === "flat"
      ? false
      : (dir === "up" && goodWhen === "up") || (dir === "down" && goodWhen === "down");
  const color =
    dir === "flat"
      ? "text-ink-500"
      : good
        ? "text-emerald-600"
        : "text-rose-600";
  return (
    <li className="flex items-center gap-3 py-2.5">
      <div className="h-8 w-8 rounded-lg bg-ink-100 flex items-center justify-center text-ink-600 text-xs font-semibold shrink-0">
        {name
          .split(" ")
          .slice(0, 2)
          .map((s) => s[0])
          .join("")
          .toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-ink-900 truncate">{name}</div>
        <div className="text-xs text-ink-500">This month</div>
      </div>
      <div className="text-right">
        <div className="text-sm font-semibold text-ink-900 tabular">
          {formatINRShort(amount)}
        </div>
        <div className={cn("text-xs font-medium tabular", color)}>
          {dir === "flat" ? "flat" : `${deltaPct > 0 ? "+" : ""}${deltaPct.toFixed(0)}%`}
        </div>
      </div>
    </li>
  );
}

function ComplianceRow({ status, label }: { status: "ok" | "warn" | "fail"; label: string }) {
  const m = {
    ok: { dot: "bg-emerald-500", cls: "text-ink-700" },
    warn: { dot: "bg-amber-500", cls: "text-ink-700" },
    fail: { dot: "bg-rose-500", cls: "text-ink-700" },
  }[status];
  return (
    <li className="flex items-center gap-2 text-sm">
      <span className={cn("h-2 w-2 rounded-full shrink-0", m.dot)} />
      <span className={m.cls}>{label}</span>
    </li>
  );
}

function EmptyChart({ label }: { label: string }) {
  return (
    <div className="h-48 flex items-center justify-center text-sm text-ink-500 rounded-lg bg-ink-50">
      {label}
    </div>
  );
}

// Keep Coins import alive for tree-shaking visibility (used elsewhere).
void Coins;
