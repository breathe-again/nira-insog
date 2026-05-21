/** Regular Outflows — what the system has learned recurs monthly.
 *
 * Surfaces patterns the recurring detector finds in the bank-statement
 * history: rent, salary, AWS, electricity, etc. Status chips tell the
 * founder which are on track, due soon, or overdue.
 */

import { AlertTriangle, CalendarClock, CheckCircle2, Repeat } from "lucide-react";
import type { RecurringOutflowOut } from "../types";
import { formatINRShort } from "../lib/format";
import { cn } from "../lib/cn";

interface Props {
  rows: RecurringOutflowOut[];
}

const STATUS_META: Record<
  RecurringOutflowOut["status"],
  { label: string; chip: string; Icon: typeof CheckCircle2 }
> = {
  on_track: {
    label: "On track",
    chip: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    Icon: CheckCircle2,
  },
  due_soon: {
    label: "Due soon",
    chip: "bg-amber-50 text-amber-700 ring-amber-200",
    Icon: CalendarClock,
  },
  overdue: {
    label: "Overdue",
    chip: "bg-rose-50 text-rose-700 ring-rose-200",
    Icon: AlertTriangle,
  },
};

export default function RecurringOutflows({ rows }: Props) {
  if (!rows.length) {
    return (
      <div className="text-sm text-ink-500 text-center py-8">
        <Repeat className="h-5 w-5 text-ink-400 mx-auto mb-2" />
        No recurring patterns detected yet. Need at least 3 months of bank
        statements to learn your regular outflows.
      </div>
    );
  }

  return (
    <ul className="space-y-2.5">
      {rows.map((r) => {
        const meta = STATUS_META[r.status] ?? STATUS_META.on_track;
        const amount =
          typeof r.median_amount === "number"
            ? r.median_amount
            : parseFloat(r.median_amount);
        return (
          <li
            key={`${r.label}-${r.expected_day_of_month}`}
            className="flex items-center gap-3 py-2 px-3 rounded-xl ring-1 ring-ink-100 hover:bg-ink-50"
          >
            <div
              className={cn(
                "h-9 w-9 rounded-xl flex items-center justify-center shrink-0 ring-1",
                meta.chip,
              )}
            >
              <meta.Icon className="h-4 w-4" />
            </div>

            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-ink-900 truncate">
                {r.label}
              </div>
              <div className="text-[11px] text-ink-500">
                {r.expected_day_of_month
                  ? `Around day ${r.expected_day_of_month} each month`
                  : "Monthly"}
                {" · "}
                seen {r.observed_count} times
              </div>
            </div>

            <div className="text-right shrink-0">
              <div className="text-sm font-semibold text-ink-900 tabular">
                {formatINRShort(amount)}
              </div>
              <div
                className={cn(
                  "text-[10px] uppercase tracking-wider rounded-full px-1.5 py-0.5 inline-block mt-0.5 ring-1",
                  meta.chip,
                )}
              >
                {meta.label}
                {r.status !== "on_track" && r.days_until_due !== null && (
                  <span className="ml-1 normal-case tracking-normal">
                    {r.days_until_due < 0
                      ? `${Math.abs(r.days_until_due)}d late`
                      : `in ${r.days_until_due}d`}
                  </span>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
