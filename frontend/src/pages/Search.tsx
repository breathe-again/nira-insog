/** Semantic search over the tenant's bank transactions.
 *
 * Type a natural-language query — "rent", "AWS", "salary March", "Abhijit",
 * "Swiggy in March" — get back the matching txns ranked by semantic
 * similarity. Works even when the bank description doesn't contain the
 * literal word (e.g. searching "rent" finds payments to your landlord
 * by name).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Search as SearchIcon, Sparkles } from "lucide-react";
import TopBar from "../components/TopBar";
import { api } from "../api";
import type { SearchHitOut } from "../types";
import { formatINRShort } from "../lib/format";
import { cn } from "../lib/cn";

const EXAMPLES = [
  "rent",
  "AWS",
  "salary",
  "Swiggy",
  "credit card payment",
  "transfer to Abhijit",
  "income tax",
];

export default function Search() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHitOut[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  const runSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setHits(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await api.search(q, 30);
      setEnabled(res.enabled);
      setHits(res.hits);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
      setHits([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Debounced live-search as the user types.
  useEffect(() => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      void runSearch(query);
    }, 350);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, [query, runSearch]);

  return (
    <>
      <TopBar
        title="Search"
        subtitle="Semantic search across all your transactions"
      />

      <div className="p-6 space-y-6">
        <div className="relative">
          <SearchIcon className="absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 text-ink-400" />
          <input
            type="text"
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask in plain English: rent, AWS, salary, transfers to Abhijit…"
            className="w-full pl-12 pr-4 h-14 rounded-2xl ring-1 ring-ink-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-brand-600"
          />
          {loading && (
            <Loader2 className="absolute right-4 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-400 animate-spin" />
          )}
        </div>

        {!query && (
          <div className="rounded-2xl bg-white ring-1 ring-ink-200 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-900">
              <Sparkles className="h-4 w-4 text-brand-600" />
              Try an example
            </div>
            <div className="flex flex-wrap gap-2 mt-3">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => setQuery(ex)}
                  className="px-3 py-1.5 rounded-lg text-xs text-ink-700 bg-ink-50 ring-1 ring-ink-200 hover:bg-ink-100"
                >
                  {ex}
                </button>
              ))}
            </div>
            <p className="text-xs text-ink-500 mt-4">
              Powered by sentence-transformer embeddings. Finds transactions
              even when descriptions don't contain the exact word.
            </p>
          </div>
        )}

        {enabled === false && (
          <div className="rounded-xl bg-amber-50 ring-1 ring-amber-200 text-amber-800 px-4 py-3 text-sm">
            Semantic search is not enabled on the server yet — go to{" "}
            <b>Learning</b> → run <b>Backfill embeddings</b> to switch it on.
          </div>
        )}

        {error && (
          <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 text-rose-700 px-4 py-3 text-sm">
            {error}
          </div>
        )}

        {hits && hits.length === 0 && query.trim() && (
          <div className="rounded-2xl bg-white ring-1 ring-ink-200 p-12 text-center text-sm text-ink-500">
            No transactions match <b className="text-ink-700">"{query}"</b>.
          </div>
        )}

        {hits && hits.length > 0 && (
          <div className="rounded-2xl bg-white ring-1 ring-ink-200 overflow-hidden">
            <div className="px-4 py-3 border-b border-ink-100 flex items-center justify-between">
              <span className="text-xs uppercase tracking-wider text-ink-500">
                {hits.length} match{hits.length === 1 ? "" : "es"}
              </span>
              <span className="text-[10px] text-ink-400">
                Ranked by semantic similarity
              </span>
            </div>
            <ul className="divide-y divide-ink-100">
              {hits.map((hit) => (
                <SearchRow key={hit.id} hit={hit} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </>
  );
}

function SearchRow({ hit }: { hit: SearchHitOut }) {
  const amount =
    hit.amount !== null && hit.amount !== undefined
      ? parseFloat(hit.amount)
      : 0;
  const distanceLabel = hit.distance !== null
    ? hit.distance < 0.15
      ? "Strong match"
      : hit.distance < 0.3
        ? "Good match"
        : hit.distance < 0.5
          ? "Loose match"
          : "Weak match"
    : "";
  const matchTone = hit.distance !== null
    ? hit.distance < 0.15
      ? "bg-emerald-50 text-emerald-700"
      : hit.distance < 0.3
        ? "bg-brand-50 text-brand-700"
        : hit.distance < 0.5
          ? "bg-amber-50 text-amber-700"
          : "bg-ink-100 text-ink-600"
    : "bg-ink-100 text-ink-600";

  return (
    <li className="px-4 py-3 flex items-center gap-3 hover:bg-ink-50">
      <div
        className={cn(
          "h-2 w-2 rounded-full shrink-0",
          hit.direction === "debit" ? "bg-rose-400" : "bg-emerald-400",
        )}
      />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-ink-900 truncate">{hit.description}</div>
        <div className="text-[11px] text-ink-500 mt-0.5 flex items-center gap-2">
          <span>{hit.txn_date ?? "—"}</span>
          {hit.category && (
            <span className="px-1.5 py-0.5 rounded bg-ink-100 text-ink-600">
              {hit.category}
            </span>
          )}
        </div>
      </div>
      <div className="text-right shrink-0">
        <div
          className={cn(
            "text-sm font-semibold tabular",
            hit.direction === "debit" ? "text-rose-600" : "text-emerald-600",
          )}
        >
          {hit.direction === "debit" ? "−" : "+"}
          {formatINRShort(amount)}
        </div>
        {distanceLabel && (
          <div
            className={cn(
              "text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5 inline-block mt-0.5",
              matchTone,
            )}
          >
            {distanceLabel}
          </div>
        )}
      </div>
    </li>
  );
}
