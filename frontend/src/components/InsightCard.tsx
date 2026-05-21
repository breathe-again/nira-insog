import { AlertTriangle, Info, Sparkles, X } from "lucide-react";
import { cn } from "../lib/cn";

export type Severity = "info" | "attention" | "urgent";

interface Props {
  severity: Severity;
  title: string;
  body: string;
  time: string;
  onDismiss?: () => void;
}

const META = {
  info: { Icon: Info, ring: "ring-ink-200", chip: "bg-ink-100 text-ink-700", iconWrap: "bg-ink-100 text-ink-700" },
  attention: {
    Icon: Sparkles,
    ring: "ring-amber-200",
    chip: "bg-amber-100 text-amber-800",
    iconWrap: "bg-amber-100 text-amber-700",
  },
  urgent: {
    Icon: AlertTriangle,
    ring: "ring-rose-200",
    chip: "bg-rose-100 text-rose-800",
    iconWrap: "bg-rose-100 text-rose-700",
  },
} as const;

export default function InsightCard({ severity, title, body, time, onDismiss }: Props) {
  const m = META[severity];
  return (
    <div className={cn("rounded-xl bg-white ring-1 p-4 flex gap-3 group", m.ring)}>
      <div className={cn("h-8 w-8 shrink-0 rounded-lg flex items-center justify-center", m.iconWrap)}>
        <m.Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold text-ink-900 truncate">{title}</h4>
          <span className={cn("chip", m.chip)}>{severity}</span>
        </div>
        <p className="text-sm text-ink-600 mt-1 leading-snug">{body}</p>
        <div className="text-[11px] text-ink-500 mt-2">{time}</div>
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="opacity-0 group-hover:opacity-100 h-7 w-7 rounded-md text-ink-500 hover:bg-ink-100 flex items-center justify-center transition-opacity"
          aria-label="Dismiss"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
