/** Learning & Training status page.
 *
 * Surfaces every Tier-1 learning signal so the founder can see what the
 * system has learned from their uploads — without SSH'ing to the server.
 *
 * Sections:
 *   - Headline counters (txns, patterns, tagged, auto-categorized, insights)
 *   - Adaptive anomaly threshold with plain-English explanation
 *   - Detected recurring patterns with overdue status
 *   - 30-day forecast preview chart highlighting recurring-payment days
 *   - "Retrain on existing data" button
 */

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  Gauge,
  Loader2,
  Play,
  Repeat,
  Sparkles,
  TrendingDown,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import TopBar from "../components/TopBar";
import { api } from "../api";
import type { LearningStatusOut, RetrainOut } from "../types";
import { formatINRShort } from "../lib/format";
import { cn } from "../lib/cn";

export default function Learning() {
  const [status, setStatus] = useState<LearningStatusOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retraining, setRetraining] = useState(false);
  const [lastRetrain, setLastRetrain] = useState<RetrainOut | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await api.learningStatus();
      setStatus(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load learning status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleRetrain() {
    setRetraining(true);
    setError(null);
    try {
      const result = await api.retrain();
      setLastRetrain(result);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Retrain failed");
    } finally {
      setRetraining(false);
    }
  }

  return (
    <>
      <TopBar
        title="Learning & Training"
        subtitle="What the system has learned from your data"
        actions={
          <button
            type="button"
            onClick={handleRetrain}
            disabled={retraining}
            className="inline-flex items-center gap-2 px-3 h-9 rounded-lg bg-brand-600 text-white text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
          >
            {retraining ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {retraining ? "Retraining…" : "Retrain on existing data"}
          </button>
        }
      />

      <div className="p-6 space-y-6">
        {error && (
          <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 text-rose-700 px-4 py-3 text-sm">
            {error}
          </div>
        )}

        {lastRetrain && (
          <div className="rounded-xl bg-emerald-50 ring-1 ring-emerald-200 text-emerald-800 px-4 py-3 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            <span className="font-medium">Retrain complete.</span>
            <span>
              Patterns: <b>{lastRetrain.new_patterns}</b>
            </span>
            <span>
              Newly tagged: <b>{lastRetrain.newly_tagged_txns}</b>
            </span>
            <span>
              Auto-categorized: <b>{lastRetrain.auto_categorized}</b>
            </span>
            <span>
              Missed-payment alerts: <b>{lastRetrain.missed_payment_insights}</b>
            </span>
            <span>
              Insights rewritten in plain English: <b>{lastRetrain.rehumanized_insights}</b>
            </span>
          </div>
        )}

        {loading && !status ? (
          <div className="flex items-center justify-center py-16 text-ink-400">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : !status ? null : (
          <>
            {/* Headline counters */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <StatTile
                label="Bank transactions"
                value={status.bank_txn_count}
                Icon={Database}
                tone="brand"
                hint="Ingested into the system"
              />
              <StatTile
                label="Recurring patterns"
                value={status.pattern_count}
                Icon={Repeat}
                tone="emerald"
                hint="Detected monthly outflows"
              />
              <StatTile
                label="Auto-categorized"
                value={status.auto_categorized_count}
                Icon={Sparkles}
                tone="violet"
                hint="Tagged from vendor defaults"
              />
              <StatTile
                label="Insights generated"
                value={status.insight_count}
                Icon={AlertTriangle}
                tone="amber"
                hint={`${status.anomaly_insight_count} anomalies · ${status.missed_payment_insight_count} missed`}
              />
            </div>

            {/* Adaptive threshold */}
            <div className="bg-white rounded-2xl ring-1 ring-ink-200 p-5">
              <div className="flex items-start gap-3">
                <div className="h-10 w-10 rounded-xl bg-brand-50 text-brand-700 flex items-center justify-center shrink-0">
                  <Gauge className="h-5 w-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-ink-900">
                    Adaptive anomaly threshold
                  </div>
                  <div className="text-xs text-ink-500 mt-0.5">
                    Tuned automatically to your business's spending variability
                  </div>
                  <p className="text-sm text-ink-700 mt-3">
                    {status.threshold_explanation}
                  </p>
                  <div className="flex items-center gap-6 mt-4">
                    <Metric
                      label="Current threshold"
                      value={`${status.adaptive_z_threshold.toFixed(1)}σ`}
                    />
                    <Metric
                      label="Coefficient of variation"
                      value={status.coefficient_of_variation.toFixed(2)}
                    />
                    <Metric
                      label="Tagged as recurring"
                      value={status.tagged_txn_count.toString()}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Detected patterns */}
            <div className="bg-white rounded-2xl ring-1 ring-ink-200 p-5">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-ink-900">
                    Detected recurring patterns
                  </h3>
                  <p className="text-xs text-ink-500 mt-0.5">
                    Each row was learned from at least 3 matching payments
                  </p>
                </div>
                <span className="text-xs text-ink-500 tabular">
                  {status.patterns.length} patterns
                </span>
              </div>
              {status.patterns.length === 0 ? (
                <div className="text-sm text-ink-500 text-center py-8">
                  <Repeat className="h-5 w-5 text-ink-400 mx-auto mb-2" />
                  No patterns yet — upload at least 3 months of bank statements,
                  then click <b>Retrain on existing data</b> above.
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-ink-500 border-b border-ink-100">
                      <th className="py-2 font-medium">Vendor / Label</th>
                      <th className="py-2 font-medium">Typical amount</th>
                      <th className="py-2 font-medium">Day</th>
                      <th className="py-2 font-medium">Observed</th>
                      <th className="py-2 font-medium">Last seen</th>
                      <th className="py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.patterns.map((p) => (
                      <tr key={`${p.label}-${p.expected_day_of_month}`} className="border-b border-ink-50">
                        <td className="py-2.5 text-ink-900 font-medium">{p.label}</td>
                        <td className="py-2.5 text-ink-900 tabular">
                          {formatINRShort(
                            typeof p.median_amount === "number"
                              ? p.median_amount
                              : parseFloat(p.median_amount),
                          )}
                        </td>
                        <td className="py-2.5 text-ink-700 tabular">
                          {p.expected_day_of_month ?? "—"}
                        </td>
                        <td className="py-2.5 text-ink-700 tabular">{p.observed_count}×</td>
                        <td className="py-2.5 text-ink-500">
                          {p.last_seen_on}
                          <span className="text-ink-400 ml-1">
                            ({p.days_since_last_seen}d ago)
                          </span>
                        </td>
                        <td className="py-2.5">
                          {p.is_overdue ? (
                            <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider rounded-full px-1.5 py-0.5 bg-rose-50 text-rose-700 ring-1 ring-rose-200">
                              <Clock className="h-3 w-3" />
                              Overdue
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider rounded-full px-1.5 py-0.5 bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">
                              <CheckCircle2 className="h-3 w-3" />
                              On track
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* Forecast preview */}
            <div className="bg-white rounded-2xl ring-1 ring-ink-200 p-5">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h3 className="text-sm font-semibold text-ink-900">
                    Forecast preview · next 30 days
                  </h3>
                  <p className="text-xs text-ink-500 mt-0.5">
                    Yellow dots = days the system expects a recurring outflow.
                  </p>
                </div>
                <TrendingDown className="h-4 w-4 text-ink-400" />
              </div>
              {status.forecast_preview.length === 0 ? (
                <div className="text-sm text-ink-500 text-center py-8">
                  Not enough history yet for a useful forecast.
                </div>
              ) : (
                <div className="h-56">
                  <ResponsiveContainer>
                    <AreaChart
                      data={status.forecast_preview.map((p) => ({
                        date: p.date.slice(5),
                        forecast: Number(p.forecast),
                        isRecurringDay: p.is_recurring_day,
                      }))}
                    >
                      <defs>
                        <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#6366f1" stopOpacity={0.3} />
                          <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                      <XAxis dataKey="date" stroke="#94a3b8" fontSize={11} />
                      <YAxis
                        stroke="#94a3b8"
                        fontSize={11}
                        tickFormatter={(v) => formatINRShort(v)}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: "white",
                          border: "1px solid #e2e8f0",
                          borderRadius: 12,
                          fontSize: 12,
                        }}
                        formatter={(value: number) => formatINRShort(value)}
                      />
                      <Area
                        type="monotone"
                        dataKey="forecast"
                        stroke="#6366f1"
                        fill="url(#g1)"
                        strokeWidth={2}
                      />
                      {status.forecast_preview
                        .filter((p) => p.is_recurring_day)
                        .map((p, i) => (
                          <ReferenceDot
                            key={i}
                            x={p.date.slice(5)}
                            y={Number(p.forecast)}
                            r={4}
                            fill="#f59e0b"
                            stroke="#fbbf24"
                          />
                        ))}
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            {/* Footer hint */}
            <div className="rounded-xl bg-ink-50 px-4 py-3 text-xs text-ink-600 flex items-start gap-2">
              <Activity className="h-3.5 w-3.5 mt-0.5 text-ink-500 shrink-0" />
              <span>
                Training runs automatically after every bank-statement upload.
                Use the <b>Retrain on existing data</b> button if you've changed
                vendor categories or just want to refresh the patterns without
                uploading anything new.
              </span>
            </div>
          </>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface StatTileProps {
  label: string;
  value: number;
  Icon: typeof Database;
  tone: "brand" | "emerald" | "violet" | "amber";
  hint?: string;
}

function StatTile({ label, value, Icon, tone, hint }: StatTileProps) {
  const toneClass = {
    brand: "from-brand-50 to-white ring-brand-100 text-brand-700",
    emerald: "from-emerald-50 to-white ring-emerald-100 text-emerald-700",
    violet: "from-violet-50 to-white ring-violet-100 text-violet-700",
    amber: "from-amber-50 to-white ring-amber-100 text-amber-700",
  }[tone];

  return (
    <div
      className={cn(
        "rounded-2xl ring-1 bg-gradient-to-br p-4 flex flex-col gap-2",
        toneClass,
      )}
    >
      <div className="flex items-center justify-between">
        <Icon className="h-5 w-5" />
        <span className="text-[10px] uppercase tracking-wider opacity-80">
          {label}
        </span>
      </div>
      <div className="text-2xl font-semibold text-ink-900 tabular">{value}</div>
      {hint && <div className="text-[11px] text-ink-500">{hint}</div>}
    </div>
  );
}

interface MetricProps {
  label: string;
  value: string;
}

function Metric({ label, value }: MetricProps) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-ink-500">
        {label}
      </div>
      <div className="text-lg font-semibold text-ink-900 tabular">{value}</div>
    </div>
  );
}
