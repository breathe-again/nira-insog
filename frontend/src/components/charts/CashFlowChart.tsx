import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatINRShort } from "../../lib/format";

interface Row {
  date: string;
  in: number;
  out: number;
  net: number;
}

export default function CashFlowChart({ data }: { data: Row[] }) {
  const chartWidth = Math.max(600, data.length * 12);
  const labelInterval = Math.max(0, Math.floor(data.length / 15) - 1);

  return (
    <div className="w-full overflow-x-auto rounded-lg">
      <div style={{ width: chartWidth, height: 280, minWidth: "100%" }}>
        <ResponsiveContainer width="100%" height="100%">
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
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "#64748b" }}
              tickLine={false}
              axisLine={{ stroke: "#e2e8f0" }}
              interval={labelInterval}
              angle={-40}
              textAnchor="end"
              height={48}
            />
            <YAxis
              tick={{ fontSize: 11, fill: "#64748b" }}
              tickFormatter={(v) => formatINRShort(v)}
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
              formatter={(value: number, name) => [formatINRShort(value), name === "in" ? "Inflow" : "Outflow"]}
            />
            <Area
              type="monotone"
              dataKey="in"
              stroke="#10b981"
              strokeWidth={2}
              fill="url(#inGrad)"
            />
            <Area
              type="monotone"
              dataKey="out"
              stroke="#ef4444"
              strokeWidth={2}
              fill="url(#outGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
