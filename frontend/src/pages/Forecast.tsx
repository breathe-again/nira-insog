/** 13-week cash forecast page.
 *
 * One headline chart + two supporting sections:
 *
 *  1. Headline strip — starting cash, projected end-of-horizon cash
 *     (likely / pessimistic / optimistic), runway-zero warning.
 *  2. Forecast chart — 91 days of cash position with three scenario
 *     bands (pessimistic/likely/optimistic), inflows/outflows as small
 *     bars at the bottom for context.
 *  3. Driver list — "Why this forecast?" panel listing the largest
 *     projected inflows + outflows, with confidence + source.
 *
 * The CFO uses this in two modes:
 *   a) "Where will my cash be?" — glance at the chart, see runway.
 *   b) "Why is it doing that?" — click a date on the chart, see which
 *      drivers caused that day's swing. (Future: click-through tooltip.)
 *
 * Comparable products: Trovata, Drivetrain, Cube, Vena. We match their
 * surface area for mid-market Indian SMBs at 1/5 the price.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpToLine,
  CalendarClock,
  Info,
  Loader2,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  Wallet,
} from "lucide-react";

import TopBar from "../components/TopBar";
import SectionCard from "../components/SectionCard";
import EmptyState from "../components/EmptyState";
import { api } from "../api";
import type {
  CashForecastOut,
  CashForecastPointOut,
  ForecastDriverOut,
} from "../types";
import { formatINR, formatINRShort } from "../lib/format";
import { cn } from "../lib/cn";


type ChartPoint = {
  date: string;
  label: string;       // "Wk 4" or "Jul 1"
  pessimistic: number;
  likely: number;
  optimistic: number;
  spread: number;      // optimistic - pessimistic, for area shading
  inflow: number;
  outflow: number;
  actual: number | null;
};


export default function Forecast() {
  const [forecast, setForecast] = useState<CashForecastOut | null>(null);
  const [drivers, setDrivers] = useState<ForecastDriverOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const f = await api.getCashForecast();
      setForecast(f);
      if (f) {
        const d = await api.getForecastDrivers();
        setDrivers(d);
      } else {
        setDrivers([]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRun = useCallback(async () => {
    setGenerating(true);
    setError(null);
    try {
      const f = await api.runCashForecast(91);
      setForecast(f);
      const d = await api.getForecastDrivers();
      setDrivers(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }, []);

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">
              13-week cash forecast
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">
              Projects your cash position over the next 91 days using your
              recurring patterns, open invoices, and the Indian tax
              calendar. Pessimistic / likely / optimistic bands reflect AR
              lateness, AP stretching, and amount variability.
            </p>
          </div>
          <button
            type="button"
            onClick={handleRun}
            disabled={generating}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm",
              "hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-400",
            )}
          >
            {generating ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            {forecast ? "Refresh forecast" : "Generate forecast"}
          </button>
        </header>

        {error && (
          <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex h-64 items-center justify-center text-slate-500">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        ) : !forecast ? (
          <EmptyState
            title="No forecast yet"
            description="Click 'Generate forecast' above to build your first 13-week cash projection. It runs in a few seconds — we'll pull in your recurring patterns, open invoices, and tax calendar."
            icon={Wallet}
          />
        ) : (
          <>
            <HeadlineStrip forecast={forecast} />
            <SectionCard
              title="Cash trajectory — next 91 days"
              subtitle={`As of ${formatDate(forecast.as_of_date)}. Three scenarios shaded; today's cash is ${formatINR(forecast.starting_cash_inr)}.`}
            >
              <ForecastChart points={forecast.points} />
            </SectionCard>
            <DriversSection drivers={drivers} />
          </>
        )}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------


function HeadlineStrip({ forecast }: { forecast: CashForecastOut }) {
  const starting = Number(forecast.starting_cash_inr);
  const ending = Number(forecast.ending_cash_likely_inr);
  const endingPess = Number(forecast.ending_cash_pessimistic_inr);
  const endingOpt = Number(forecast.ending_cash_optimistic_inr);
  const delta = ending - starting;
  const dropping = delta < 0;

  return (
    <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <KpiTile
        label="Cash today"
        value={formatINR(starting)}
        icon={Wallet}
        accent="slate"
      />
      <KpiTile
        label={`Cash on day ${forecast.horizon_days}`}
        value={formatINR(ending)}
        sub={`Range: ${formatINRShort(endingPess)} – ${formatINRShort(endingOpt)}`}
        icon={dropping ? TrendingDown : TrendingUp}
        accent={dropping ? "rose" : "emerald"}
      />
      <KpiTile
        label="Total inflows expected"
        value={formatINR(forecast.inflows_total_inr)}
        sub={`From ${forecast.drivers_count} drivers`}
        icon={ArrowDownToLine}
        accent="emerald"
      />
      <KpiTile
        label="Total outflows expected"
        value={formatINR(forecast.outflows_total_inr)}
        icon={ArrowUpToLine}
        accent="rose"
      />
      {forecast.runway_zero_date && (
        <div className="col-span-full rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-5 w-5 flex-none text-amber-600" />
            <div>
              <p className="font-medium">
                Cash projected to hit ₹0 on {formatDate(forecast.runway_zero_date)}
              </p>
              <p className="mt-1">
                In the likely scenario, your cash crosses zero before the end of
                the horizon. Review the drivers below to find the largest swings
                you can defer or accelerate.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


function ForecastChart({ points }: { points: CashForecastPointOut[] }) {
  const data = useMemo<ChartPoint[]>(
    () =>
      points.map((p) => {
        const pess = Number(p.pessimistic);
        const opt = Number(p.optimistic);
        return {
          date: p.date,
          label: formatDateShort(p.date),
          pessimistic: pess,
          likely: Number(p.likely),
          optimistic: opt,
          spread: Math.max(0, opt - pess),
          inflow: Number(p.inflow),
          outflow: Number(p.outflow),
          actual: p.actual !== null ? Number(p.actual) : null,
        };
      }),
    [points],
  );

  // For visual clarity show every 7th label on the X-axis (one per week)
  const tickInterval = Math.max(1, Math.floor(data.length / 13));

  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis
            dataKey="label"
            interval={tickInterval}
            tick={{ fontSize: 12, fill: "#64748b" }}
          />
          <YAxis
            tickFormatter={(v) => formatINRShort(v as number)}
            tick={{ fontSize: 12, fill: "#64748b" }}
          />
          <Tooltip content={<ChartTooltip />} />
          <Legend wrapperStyle={{ fontSize: 12 }} />

          {/* Pessimistic-Optimistic band — drawn as a stacked area. */}
          <Area
            type="monotone"
            dataKey="pessimistic"
            stackId="band"
            stroke="none"
            fill="transparent"
            isAnimationActive={false}
            legendType="none"
          />
          <Area
            type="monotone"
            dataKey="spread"
            name="Scenario range"
            stackId="band"
            stroke="none"
            fill="#c7d2fe"
            fillOpacity={0.45}
            isAnimationActive={false}
          />

          {/* Three scenario lines on top */}
          <Line
            type="monotone"
            dataKey="pessimistic"
            name="Pessimistic"
            stroke="#dc2626"
            strokeWidth={1.5}
            strokeDasharray="4 2"
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="likely"
            name="Likely"
            stroke="#4f46e5"
            strokeWidth={2.5}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="optimistic"
            name="Optimistic"
            stroke="#059669"
            strokeWidth={1.5}
            strokeDasharray="4 2"
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="actual"
            name="Actual"
            stroke="#0f172a"
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}


function ChartTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: Array<{ payload: ChartPoint }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const hasFlows = p.inflow > 0 || p.outflow > 0;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs shadow-lg">
      <div className="mb-1 font-medium text-slate-900">{label}</div>
      <div className="space-y-0.5">
        <div className="text-emerald-700">Optimistic: {formatINR(p.optimistic)}</div>
        <div className="font-medium text-indigo-700">Likely: {formatINR(p.likely)}</div>
        <div className="text-rose-700">Pessimistic: {formatINR(p.pessimistic)}</div>
        {p.actual !== null && (
          <div className="text-slate-900">Actual: {formatINR(p.actual)}</div>
        )}
      </div>
      {hasFlows && (
        <div className="mt-2 border-t border-slate-100 pt-2 text-slate-600">
          {p.inflow > 0 && <div>+ {formatINR(p.inflow)} inflow</div>}
          {p.outflow > 0 && <div>− {formatINR(p.outflow)} outflow</div>}
        </div>
      )}
    </div>
  );
}


function DriversSection({ drivers }: { drivers: ForecastDriverOut[] }) {
  const sorted = useMemo(
    () =>
      [...drivers].sort(
        (a, b) =>
          Number(b.expected_amount_inr) - Number(a.expected_amount_inr),
      ),
    [drivers],
  );

  if (sorted.length === 0) {
    return null;
  }

  const inflows = sorted.filter((d) => d.direction === "inflow").slice(0, 12);
  const outflows = sorted.filter((d) => d.direction === "outflow").slice(0, 12);

  return (
    <SectionCard
      title="Why this forecast?"
      subtitle={`${drivers.length} drivers feeding the projection — biggest items shown first.`}
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <DriverList title="Expected inflows" drivers={inflows} accent="emerald" />
        <DriverList title="Expected outflows" drivers={outflows} accent="rose" />
      </div>
    </SectionCard>
  );
}


function DriverList({
  title,
  drivers,
  accent,
}: {
  title: string;
  drivers: ForecastDriverOut[];
  accent: "emerald" | "rose";
}) {
  const accentClasses =
    accent === "emerald"
      ? "text-emerald-700 bg-emerald-50 border-emerald-100"
      : "text-rose-700 bg-rose-50 border-rose-100";

  return (
    <div>
      <h3 className="mb-3 text-sm font-medium text-slate-700">{title}</h3>
      {drivers.length === 0 ? (
        <p className="text-sm text-slate-500">None in the horizon.</p>
      ) : (
        <ul className="space-y-2">
          {drivers.map((d) => (
            <li
              key={d.id}
              className={cn(
                "flex items-start justify-between rounded-lg border p-3",
                accentClasses,
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{d.label}</div>
                <div className="mt-1 flex items-center gap-2 text-xs">
                  <CalendarClock className="h-3.5 w-3.5" />
                  <span>
                    {d.expected_date ? formatDate(d.expected_date) : "—"}
                  </span>
                  <span className="text-slate-400">·</span>
                  <span className="text-slate-500">{driverKindLabel(d.kind)}</span>
                  <span className="text-slate-400">·</span>
                  <span className="text-slate-500">
                    {Math.round(Number(d.confidence) * 100)}% conf.
                  </span>
                </div>
              </div>
              <div className="ml-3 text-sm font-semibold tabular-nums">
                {formatINR(d.expected_amount_inr)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function KpiTile({
  label,
  value,
  sub,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: typeof Wallet;
  accent: "slate" | "emerald" | "rose";
}) {
  const accentBg = {
    slate: "bg-slate-100 text-slate-700",
    emerald: "bg-emerald-100 text-emerald-700",
    rose: "bg-rose-100 text-rose-700",
  }[accent];
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">
            {label}
          </p>
          <p className="mt-1 text-xl font-semibold text-slate-900 tabular-nums">
            {value}
          </p>
          {sub && <p className="mt-1 text-xs text-slate-500">{sub}</p>}
        </div>
        <div className={cn("rounded-md p-2", accentBg)}>
          <Icon className="h-4 w-4" />
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function driverKindLabel(kind: ForecastDriverOut["kind"]): string {
  switch (kind) {
    case "recurring_inflow":
      return "Recurring inflow";
    case "recurring_outflow":
      return "Recurring outflow";
    case "open_receivable":
      return "Open receivable";
    case "open_payable":
      return "Open payable";
    case "scheduled_tax":
      return "Tax deadline";
    case "opening_balance":
      return "Opening balance";
    case "one_off":
      return "One-off event";
    default:
      return kind;
  }
}


function formatDate(iso: string): string {
  // YYYY-MM-DD → "5 Jul 2026"
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, (m ?? 1) - 1, d);
  return date.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}


function formatDateShort(iso: string): string {
  // "5 Jul"
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, (m ?? 1) - 1, d);
  return date.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
