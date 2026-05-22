/** "What's inside this category?" drill-down panel.
 *
 * Opens when the user clicks a slice in the expense donut. Shows the top
 * vendors/descriptions that contributed, sorted by amount, with txn counts.
 * Useful for understanding the "Other" bucket especially — answers
 * "where did 97% of my money actually go?"
 */

import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";
import { api } from "../api";
import type { CategoryDetailOut } from "../types";
import { formatINRShort } from "../lib/format";

interface Props {
  category: string;
  from?: string;
  to?: string;
  onClose: () => void;
}

export default function CategoryDetailPanel({
  category,
  from,
  to,
  onClose,
}: Props) {
  const [data, setData] = useState<CategoryDetailOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .categoryDetail(category, { from, to, limit: 30 })
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [category, from, to]);

  // Lock body scroll while open.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // ESC closes.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 bg-ink-900/40 backdrop-blur-sm flex items-end sm:items-center justify-center p-0 sm:p-6"
      onClick={onClose}
    >
      <div
        className="w-full sm:max-w-2xl max-h-[85vh] bg-white rounded-t-2xl sm:rounded-2xl shadow-xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start gap-3 px-5 py-4 border-b border-ink-100">
          <div
            className="h-10 w-10 rounded-xl shrink-0"
            style={{ backgroundColor: data?.color ?? "#cbd5e1" }}
          />
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-ink-900">
              {data?.category ?? category}
            </h2>
            <p className="text-xs text-ink-500 mt-0.5">
              {data
                ? `${data.txn_count} transaction${data.txn_count === 1 ? "" : "s"} totalling ${formatINRShort(
                    typeof data.total === "number" ? data.total : parseFloat(data.total),
                  )}`
                : "Loading…"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="h-8 w-8 rounded-lg flex items-center justify-center text-ink-500 hover:bg-ink-100"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-ink-400">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : error ? (
            <div className="m-5 rounded-xl bg-rose-50 ring-1 ring-rose-200 text-rose-700 px-3 py-2 text-sm">
              {error}
            </div>
          ) : !data || data.contributors.length === 0 ? (
            <div className="text-center py-12 text-sm text-ink-500 px-5">
              No transactions matched this category in the current window.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-ink-50 text-[10px] uppercase tracking-wider text-ink-500 sticky top-0">
                <tr>
                  <th className="px-5 py-2 text-left font-medium">Vendor / Description</th>
                  <th className="px-3 py-2 text-right font-medium w-20">Txns</th>
                  <th className="px-5 py-2 text-right font-medium w-28">Total</th>
                </tr>
              </thead>
              <tbody>
                {data.contributors.map((row, idx) => {
                  const amt =
                    typeof row.total === "number"
                      ? row.total
                      : parseFloat(row.total);
                  return (
                    <tr
                      key={`${row.vendor_name ?? "_"}-${idx}`}
                      className="border-b border-ink-50 hover:bg-ink-50"
                    >
                      <td className="px-5 py-2.5">
                        <div className="font-medium text-ink-900">
                          {row.vendor_name ?? "(unlabeled)"}
                        </div>
                        {row.description_sample && (
                          <div className="text-[11px] text-ink-500 mt-0.5 truncate max-w-[420px]">
                            {row.description_sample}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right text-ink-700 tabular">
                        {row.txn_count}
                      </td>
                      <td className="px-5 py-2.5 text-right text-ink-900 font-semibold tabular">
                        {formatINRShort(amt)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer hint */}
        <div className="px-5 py-3 border-t border-ink-100 text-[11px] text-ink-500">
          Tip: to permanently move a vendor into a different category, open
          the vendor in the Inbox and set its default category. The dashboard
          will pick up your change on the next refresh.
        </div>
      </div>
    </div>
  );
}
