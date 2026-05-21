/** Date-range picker for dashboard filters.
 *
 * Quick-pick chips:
 *   - This month             (from: 1st of current month → today)
 *   - This quarter           (Apr-Jun, Jul-Sep, Oct-Dec, Jan-Mar)
 *   - FY (Indian)            (Apr 1 → Mar 31 of the FY that contains today)
 *   - Last FY                (the prior Indian FY)
 *   - Last 30 days
 *   - Custom (from-to date inputs)
 *
 * Emits the selected range to the parent via onChange — parent refetches.
 */

import { useEffect, useMemo, useState } from "react";
import { Calendar, ChevronDown } from "lucide-react";
import { cn } from "../lib/cn";

export type Preset =
  | "this_month"
  | "this_quarter"
  | "fy_current"
  | "fy_previous"
  | "last_30d"
  | "last_90d"
  | "custom";

export interface DateRange {
  from: string; // ISO YYYY-MM-DD
  to: string; // ISO YYYY-MM-DD
  preset: Preset;
}

interface Props {
  value: DateRange;
  onChange: (next: DateRange) => void;
}

// ---------------------------------------------------------------------------
// Helpers — Indian FY runs Apr 1 → Mar 31
// ---------------------------------------------------------------------------

function iso(d: Date): string {
  // toISOString() converts to UTC, which silently shifts the date by a day
  // in any timezone west of UTC (and east of UTC for late-evening dates).
  // In IST (UTC+5:30), `new Date(2025, 3, 1)` is local Apr 1 00:00, which
  // becomes Mar 31 18:30 UTC, and `toISOString()` reports "2025-03-31".
  // We want the LOCAL calendar date here, so format components manually.
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}

function indianFY(d: Date): { start: Date; end: Date; label: string } {
  // If we're in Jan/Feb/Mar, FY started in April of the PREVIOUS year.
  const y = d.getMonth() < 3 ? d.getFullYear() - 1 : d.getFullYear();
  const start = new Date(y, 3, 1); // Apr 1
  const end = new Date(y + 1, 2, 31); // Mar 31
  const label = `FY ${String(y).slice(-2)}-${String(y + 1).slice(-2)}`;
  return { start, end, label };
}

function thisQuarter(d: Date): { start: Date; end: Date; label: string } {
  // Indian fiscal quarters: Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar
  const m = d.getMonth();
  let qStartMonth: number;
  let qLabel: string;
  if (m >= 3 && m <= 5) {
    qStartMonth = 3;
    qLabel = "Q1";
  } else if (m >= 6 && m <= 8) {
    qStartMonth = 6;
    qLabel = "Q2";
  } else if (m >= 9 && m <= 11) {
    qStartMonth = 9;
    qLabel = "Q3";
  } else {
    qStartMonth = 0;
    qLabel = "Q4";
  }
  const y = m >= 3 || qStartMonth !== 0 ? d.getFullYear() : d.getFullYear();
  // Q4 (Jan-Mar) belongs to the previous FY's year for label purposes.
  const start = new Date(y, qStartMonth, 1);
  const end = new Date(y, qStartMonth + 3, 0);
  return { start, end, label: qLabel };
}

export function presetToRange(preset: Preset, today: Date = new Date()): DateRange {
  switch (preset) {
    case "this_month": {
      return {
        preset,
        from: iso(startOfMonth(today)),
        to: iso(endOfMonth(today)),
      };
    }
    case "this_quarter": {
      const q = thisQuarter(today);
      return { preset, from: iso(q.start), to: iso(q.end) };
    }
    case "fy_current": {
      const fy = indianFY(today);
      return { preset, from: iso(fy.start), to: iso(fy.end) };
    }
    case "fy_previous": {
      const cur = indianFY(today);
      const prevStart = new Date(cur.start.getFullYear() - 1, 3, 1);
      const prevEnd = new Date(cur.start.getFullYear(), 2, 31);
      return { preset, from: iso(prevStart), to: iso(prevEnd) };
    }
    case "last_30d": {
      const from = new Date(today);
      from.setDate(from.getDate() - 29);
      return { preset, from: iso(from), to: iso(today) };
    }
    case "last_90d": {
      const from = new Date(today);
      from.setDate(from.getDate() - 89);
      return { preset, from: iso(from), to: iso(today) };
    }
    case "custom":
    default:
      return {
        preset,
        from: iso(startOfMonth(today)),
        to: iso(endOfMonth(today)),
      };
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const PRESETS: { label: string; value: Preset }[] = [
  { label: "This month", value: "this_month" },
  { label: "This quarter", value: "this_quarter" },
  { label: "FY 25-26", value: "fy_current" },
  { label: "Last FY", value: "fy_previous" },
  { label: "Last 30 days", value: "last_30d" },
  { label: "Last 90 days", value: "last_90d" },
  { label: "Custom…", value: "custom" },
];

export default function DateRangePicker({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [customFrom, setCustomFrom] = useState(value.from);
  const [customTo, setCustomTo] = useState(value.to);

  // Keep the FY label dynamic — when the year rolls over, the chip updates.
  const currentFYLabel = useMemo(() => indianFY(new Date()).label, []);
  const prevFYLabel = useMemo(() => {
    const cur = indianFY(new Date());
    const y = cur.start.getFullYear() - 1;
    return `FY ${String(y).slice(-2)}-${String(y + 1).slice(-2)}`;
  }, []);

  useEffect(() => {
    setCustomFrom(value.from);
    setCustomTo(value.to);
  }, [value.from, value.to]);

  const activeLabel = useMemo(() => {
    const found = PRESETS.find((p) => p.value === value.preset);
    if (!found) return "Custom";
    if (value.preset === "fy_current") return currentFYLabel;
    if (value.preset === "fy_previous") return prevFYLabel;
    return found.label;
  }, [value.preset, currentFYLabel, prevFYLabel]);

  function pick(p: Preset) {
    if (p === "custom") {
      setOpen(true);
      return;
    }
    setOpen(false);
    onChange(presetToRange(p));
  }

  function applyCustom() {
    if (!customFrom || !customTo) return;
    onChange({ preset: "custom", from: customFrom, to: customTo });
    setOpen(false);
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 px-3 h-9 rounded-lg ring-1 ring-ink-200 bg-white text-sm text-ink-800 hover:bg-ink-50"
      >
        <Calendar className="h-3.5 w-3.5 text-ink-500" />
        <span className="font-medium">{activeLabel}</span>
        <span className="text-ink-500 text-xs">
          {value.from} → {value.to}
        </span>
        <ChevronDown className="h-3.5 w-3.5 text-ink-400" />
      </button>

      {open && (
        <div
          className="absolute right-0 mt-1 w-72 bg-white rounded-xl shadow-lg ring-1 ring-ink-200 p-2 z-20"
          role="menu"
        >
          <div className="grid grid-cols-2 gap-1">
            {PRESETS.map((p) => {
              const label =
                p.value === "fy_current"
                  ? currentFYLabel
                  : p.value === "fy_previous"
                    ? prevFYLabel
                    : p.label;
              return (
                <button
                  key={p.value}
                  type="button"
                  onClick={() => pick(p.value)}
                  className={cn(
                    "text-left px-2.5 py-1.5 rounded-md text-xs font-medium",
                    p.value === value.preset
                      ? "bg-brand-50 text-brand-700"
                      : "text-ink-700 hover:bg-ink-50",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>

          {value.preset === "custom" && (
            <div className="mt-2 pt-2 border-t border-ink-100 space-y-1.5">
              <label className="block">
                <span className="text-[10px] uppercase tracking-wider text-ink-500">
                  From
                </span>
                <input
                  type="date"
                  value={customFrom}
                  onChange={(e) => setCustomFrom(e.target.value)}
                  className="w-full mt-0.5 px-2 py-1.5 rounded-md ring-1 ring-ink-200 text-xs"
                />
              </label>
              <label className="block">
                <span className="text-[10px] uppercase tracking-wider text-ink-500">
                  To
                </span>
                <input
                  type="date"
                  value={customTo}
                  onChange={(e) => setCustomTo(e.target.value)}
                  className="w-full mt-0.5 px-2 py-1.5 rounded-md ring-1 ring-ink-200 text-xs"
                />
              </label>
              <button
                type="button"
                onClick={applyCustom}
                className="w-full mt-1 px-2 py-1.5 rounded-md bg-brand-600 text-white text-xs font-medium hover:bg-brand-700"
              >
                Apply
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
