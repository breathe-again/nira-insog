import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatINRShort } from "../../lib/format";

interface Row {
  date: string;
  forecast: number;
  lowerBand: number;
  upperBand: number;
}

export default function ForecastChart({ data }: { data: Row[] }) {
  // For the area between bands, transform to a stacked area where the first
  // series is the lower band and the second is (upper - lower).
  const transformed = data.map((d) => ({
    ...d,
    band: d.upperBand - d.lowerBand,
  }));

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer>
        <ComposedChart data={transformed} margin={{ top: 10, right: 10, bottom: 0, left: -10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: "#64748b" }}
            tickLine={false}
            axisLine={{ stroke: "#e2e8f0" }}
            interval={4}
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
            }}
            formatter={(value: number, name) =>
              name === "forecast"
                ? [formatINRShort(value), "Forecast"]
                : name === "band"
                  ? null
                  : [formatINRShort(value), String(name)]
            }
          />
          <Area
            dataKey="lowerBand"
            stackId="band"
            stroke="none"
            fill="transparent"
          />
          <Area
            dataKey="band"
            stackId="band"
            stroke="none"
            fill="#6366f1"
            fillOpacity={0.12}
          />
          <Line
            type="monotone"
            dataKey="forecast"
            stroke="#4f46e5"
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
