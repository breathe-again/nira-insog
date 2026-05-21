import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { formatINRShort } from "../../lib/format";

interface Slice {
  name: string;
  value: number;
  color: string;
}

export default function ExpenseDonut({ data }: { data: Slice[] }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  return (
    <div className="flex items-center gap-4 min-w-0">
      <div className="h-44 w-44 shrink-0 relative">
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              innerRadius={48}
              outerRadius={70}
              paddingAngle={2}
              strokeWidth={0}
            >
              {data.map((d, i) => (
                <Cell key={i} fill={d.color} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                backgroundColor: "white",
                border: "1px solid #e2e8f0",
                borderRadius: 12,
                fontSize: 12,
              }}
              formatter={(value: number) => formatINRShort(value)}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <div className="text-[10px] uppercase tracking-wider text-ink-500">Total</div>
          <div className="text-base font-semibold text-ink-900 tabular">
            {formatINRShort(total)}
          </div>
        </div>
      </div>
      <ul className="flex-1 min-w-0 space-y-1.5">
        {data.map((d) => {
          const pct = total === 0 ? 0 : (d.value / total) * 100;
          return (
            <li key={d.name} className="flex items-center gap-2 text-sm">
              <span
                className="h-2.5 w-2.5 rounded-sm shrink-0"
                style={{ backgroundColor: d.color }}
              />
              <span className="text-ink-700 flex-1 truncate min-w-0">{d.name}</span>
              <span className="text-ink-500 text-xs tabular shrink-0 w-10 text-right">
                {pct.toFixed(0)}%
              </span>
              <span className="text-ink-900 font-medium tabular shrink-0 w-20 text-right whitespace-nowrap">
                {formatINRShort(d.value)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
