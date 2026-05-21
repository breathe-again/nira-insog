import type { LucideIcon } from "lucide-react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "../lib/cn";

interface Props {
  label: string;
  value: string;
  hint?: string;
  delta?: { label: string; dir: "up" | "down" | "flat" };
  /** "up" trend is good for revenue, bad for spend. Lets us color appropriately. */
  goodWhen?: "up" | "down";
  Icon?: LucideIcon;
  accent?: "indigo" | "emerald" | "amber" | "violet" | "rose";
}

const ACCENT: Record<NonNullable<Props["accent"]>, string> = {
  indigo: "bg-brand-50 text-brand-700",
  emerald: "bg-emerald-50 text-emerald-700",
  amber: "bg-amber-50 text-amber-700",
  violet: "bg-violet-50 text-violet-700",
  rose: "bg-rose-50 text-rose-700",
};

export default function StatCard({
  label,
  value,
  hint,
  delta,
  goodWhen = "up",
  Icon,
  accent = "indigo",
}: Props) {
  const trendColor =
    !delta || delta.dir === "flat"
      ? "text-ink-500"
      : (delta.dir === "up" && goodWhen === "up") ||
          (delta.dir === "down" && goodWhen === "down")
        ? "text-emerald-600"
        : "text-rose-600";

  const TrendIcon =
    !delta || delta.dir === "flat"
      ? Minus
      : delta.dir === "up"
        ? ArrowUpRight
        : ArrowDownRight;

  return (
    <div className="card card-hover p-5">
      <div className="flex items-start justify-between">
        <div className="text-xs font-medium text-ink-500 uppercase tracking-wide">
          {label}
        </div>
        {Icon && (
          <div className={cn("h-8 w-8 rounded-lg flex items-center justify-center", ACCENT[accent])}>
            <Icon className="h-4 w-4" />
          </div>
        )}
      </div>
      <div className="mt-3 text-2xl font-semibold text-ink-900 tabular">{value}</div>
      <div className="mt-1 flex items-center gap-2 text-xs">
        {delta && (
          <span className={cn("inline-flex items-center gap-0.5 font-medium tabular", trendColor)}>
            <TrendIcon className="h-3.5 w-3.5" />
            {delta.label}
          </span>
        )}
        {hint && <span className="text-ink-500">{hint}</span>}
      </div>
    </div>
  );
}
