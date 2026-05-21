/** Multi-mode cash flow chart.
 *
 * Three view modes, toggled by chips in the SectionCard header (the
 * parent passes the active mode in via prop):
 *
 *   - "net"        Simple inflow / outflow / net area chart (original).
 *   - "category"   Stacked-area: each day's outflows split by learned
 *                  category (rent, salary, AWS, food, etc.).
 *   - "anomaly"    Same as "net" but with red dot markers on days that
 *                  fired an insight, and yellow vertical dashed lines on
 *                  days a recurring pattern is expected to fire next.
 *
 * Long windows (>60 days) widen the SVG and scroll horizontally; short
 * windows render at container width.
 */

import {
  Area,
  AreaChart,
  CartesianGrid,
  Dot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatINRShort } from "../../lib/format";

export type CashFlowMode = "net" | "category" | "anomaly";

interface Row {
  date: string;
  in: number;
  out: number;
  net: number;
}

interface CategoryRow {
  date: string;
  // The dynamic key set comes from the server's category_palette.
  [category: string]: number | string;
}

interface RawCategoryRow {
  date: string;
  categories: Record<string, number>;
}

interface Props {
  mode: CashFlowMode;
  /** "net" + "anomaly" modes use this. */
  data: Row[];
  /** "category" mode uses this (one entry per day, with arbitrary cat keys). */
  categoryData?: RawCategoryRow[];
  /** Ordered [name, color] tuples for the stack — comes from server. */
  categoryPalette?: [string, string][];
  /** Dates ("MMM d") with an active urgent/attention insight. */
  anomalyDates?: string[];
  /** Day-of-month numbers that have recurring patterns; we mark each matching
   *  date in the window with a vertical dashed line. */
  recurringDays?: number[];
}

export default function CashFlowChart({
  mode,
  data,
  categoryData,
  categoryPalette,
  anomalyDates = [],
  recurringDays = [],
}: Props) {
  // Long-range layout: widen + horizontal scroll. Same logic as v1.
  const points = mode === "category" ? categoryData ?? [] : data;
  const isLongRange = points.length > 60;
  const chartWidth = isLongRange ? Math.max(900, points.length * 8) : 0;

  const tickFormatter = (value: string) => {
    if (!isLongRange) return value;
    const parts = value.split(" ");
    if (parts.length === 2 && parts[1] === "1") return parts[0];
    return "";
  };

  return (
    <div className={isLongRange ? "w-full overflow-x-auto rounded-lg" : "w-full rounded-lg"}>
      <div
        style={
          isLongRange
            ? { width: chartWidth, height: 300, minWidth: "100%" }
            : { width: "100%", height: 300 }
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          {mode === "category" ? (
            <CategoryChart
              data={categoryData ?? []}
              palette={categoryPalette ?? []}
              isLongRange={isLongRange}
              tickFormatter={tickFormatter}
              recurringDays={recurringDays}
            />
          ) : (
            <NetChart
              data={data}
              isLongRange={isLongRange}
              tickFormatter={tickFormatter}
              anomalyDates={mode === "anomaly" ? anomalyDates : []}
              recurringDays={mode === "anomaly" ? recurringDays : []}
            />
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Net flow chart (with optional anomaly markers + recurring reference lines)
// ---------------------------------------------------------------------------

function NetChart({
  data,
  isLongRange,
  tickFormatter,
  anomalyDates,
  recurringDays,
}: {
  data: Row[];
  isLongRange: boolean;
  tickFormatter: (v: string) => string;
  anomalyDates: string[];
  recurringDays: number[];
}) {
  const anomalySet = new Set(anomalyDates);

  // Find the chart points whose date label matches a recurring-day pattern.
  // Backend gives us day-of-month numbers; here we match against each point's
  // label like "Apr 5" (extracted from the second token).
  const recurringSet = new Set<string>();
  if (recurringDays.length > 0) {
    for (const p of data) {
      const parts = p.date.split(" ");
      const dom = parts.length === 2 ? parseInt(parts[1], 10) : NaN;
      if (Number.isFinite(dom) && recurringDays.includes(dom)) {
        recurringSet.add(p.date);
      }
    }
  }

  return (
    <AreaChart data={data} margin={{ top: 10, right: 10, bottom: 4, left: -10 }}>
      <defs>
        <linearGradient id="inGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#10b981" stopOpacity={0.35} />
          <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
        </linearGradient>
        <linearGradient id="outGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ef4444" stopOpacity={0.3} />
          <stop offset="100%" stopColor="#ef4444" stopOpacity={0} />
        </linearGradient>
      </defs>

      {/* Vertical dashed reference lines for recurring-payment expected days. */}
      {[...recurringSet].map((d) => (
        <ReferenceLine
          key={`rec-${d}`}
          x={d}
          stroke="#f59e0b"
          strokeDasharray="3 3"
          strokeOpacity={0.55}
        />
      ))}

      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
      <XAxis
        dataKey="date"
        tick={{ fontSize: 10, fill: "#64748b" }}
        tickLine={false}
        axisLine={{ stroke: "#e2e8f0" }}
        minTickGap={isLongRange ? 60 : 30}
        interval="preserveStartEnd"
        tickFormatter={tickFormatter}
        angle={isLongRange ? 0 : -40}
        textAnchor={isLongRange ? "middle" : "end"}
        height={isLongRange ? 30 : 48}
      />
      <YAxis
        tick={{ fontSize: 11, fill: "#64748b" }}
        tickFormatter={(v) => formatINRShort(v as number)}
        tickLine={false}
        axisLine={{ stroke: "#e2e8f0" }}
        width={56}
      />
      <Tooltip
        contentStyle={{
          backgroundColor: "white",
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          fontSize: 12,
          boxShadow: "0 4px 24px -8px rgba(15,23,42,0.08)",
        }}
        formatter={(value: number, name) => [
          formatINRShort(value),
          name === "in" ? "Inflow" : "Outflow",
        ]}
      />

      <Area
        type="monotone"
        dataKey="in"
        stroke="#10b981"
        strokeWidth={2}
        fill="url(#inGrad)"
        dot={false}
      />
      <Area
        type="monotone"
        dataKey="out"
        stroke="#ef4444"
        strokeWidth={2}
        fill="url(#outGrad)"
        // Render a fat red dot ONLY on days that have an anomaly insight.
        dot={(props: {
          cx?: number;
          cy?: number;
          payload?: Row;
          index?: number;
        }) => {
          const { cx, cy, payload, index } = props;
          if (cx == null || cy == null || !payload) {
            return <Dot key={`dot-${index ?? Math.random()}`} cx={0} cy={0} r={0} />;
          }
          if (!anomalySet.has(payload.date)) {
            return <Dot key={`dot-${payload.date}`} cx={cx} cy={cy} r={0} />;
          }
          return (
            <Dot
              key={`anom-${payload.date}`}
              cx={cx}
              cy={cy}
              r={5}
              fill="#dc2626"
              stroke="#fecaca"
              strokeWidth={2}
            />
          );
        }}
      />
    </AreaChart>
  );
}

// ---------------------------------------------------------------------------
// Stacked-by-category chart
// ---------------------------------------------------------------------------

function CategoryChart({
  data,
  palette,
  isLongRange,
  tickFormatter,
  recurringDays,
}: {
  data: RawCategoryRow[];
  palette: [string, string][];
  isLongRange: boolean;
  tickFormatter: (v: string) => string;
  recurringDays: number[];
}) {
  // Flatten the server's per-day `categories: {...}` into top-level keys so
  // recharts can dataKey into each category.
  const flat = data.map((p) => {
    const out: CategoryRow = { date: p.date };
    for (const [k, v] of Object.entries(p.categories ?? {})) {
      out[k] = typeof v === "number" ? v : parseFloat(String(v));
    }
    return out;
  });

  const recurringSet = new Set<string>();
  if (recurringDays.length > 0) {
    for (const p of data) {
      const parts = p.date.split(" ");
      const dom = parts.length === 2 ? parseInt(parts[1], 10) : NaN;
      if (Number.isFinite(dom) && recurringDays.includes(dom)) {
        recurringSet.add(p.date);
      }
    }
  }

  return (
    <AreaChart data={flat} margin={{ top: 10, right: 10, bottom: 4, left: -10 }}>
      <defs>
        {palette.map(([name, color]) => (
          <linearGradient key={name} id={`gradCat-${name}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.55} />
            <stop offset="100%" stopColor={color} stopOpacity={0.05} />
          </linearGradient>
        ))}
      </defs>

      {[...recurringSet].map((d) => (
        <ReferenceLine
          key={`rec-${d}`}
          x={d}
          stroke="#f59e0b"
          strokeDasharray="3 3"
          strokeOpacity={0.55}
        />
      ))}

      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
      <XAxis
        dataKey="date"
        tick={{ fontSize: 10, fill: "#64748b" }}
        tickLine={false}
        axisLine={{ stroke: "#e2e8f0" }}
        minTickGap={isLongRange ? 60 : 30}
        interval="preserveStartEnd"
        tickFormatter={tickFormatter}
        angle={isLongRange ? 0 : -40}
        textAnchor={isLongRange ? "middle" : "end"}
        height={isLongRange ? 30 : 48}
      />
      <YAxis
        tick={{ fontSize: 11, fill: "#64748b" }}
        tickFormatter={(v) => formatINRShort(v as number)}
        tickLine={false}
        axisLine={{ stroke: "#e2e8f0" }}
        width={56}
      />
      <Tooltip
        contentStyle={{
          backgroundColor: "white",
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          fontSize: 12,
          boxShadow: "0 4px 24px -8px rgba(15,23,42,0.08)",
        }}
        formatter={(value: number, name) => [formatINRShort(value), String(name)]}
      />

      {palette.map(([name, color]) => (
        <Area
          key={name}
          type="monotone"
          dataKey={name}
          stackId="cat"
          stroke={color}
          fill={`url(#gradCat-${name})`}
          strokeWidth={1.5}
          dot={false}
        />
      ))}
    </AreaChart>
  );
}
