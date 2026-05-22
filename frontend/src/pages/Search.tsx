/** Semantic search over the tenant's bank transactions.
 *
 * Type a natural-language query — "rent", "AWS", "salary March", "Abhijit",
 * "Swiggy in March" — get back the matching txns ranked by semantic
 * similarity. Works even when the bank description doesn't contain the
 * literal word (e.g. searching "rent" finds payments to your landlord
 * by name).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  CheckCircle2,
  Loader2,
  Search as SearchIcon,
  Sparkles,
} from "lucide-react";
import TopBar from "../components/TopBar";
import { api } from "../api";
import type { EmbeddingCoverageOut, SearchHitOut } from "../types";
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

  // Coverage state — drives the "Run backfill" banner that explains 0 results
  // BEFORE the user gets confused.
  const [coverage, setCoverage] = useState<EmbeddingCoverageOut | null>(null);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillNote, setBackfillNote] = useState<string | null>(null);

  // Probe coverage on mount so we know whether to show the empty-search
  // explanation banner.
  const refreshCoverage = useCallback(async () => {
    try {
      const s = await api.learningStatus();
      setCoverage(s.embedding_coverage);
      setEnabled(s.embedding_coverage.enabled);
    } catch {
      // non-fatal — coverage stays null, banner hides
    }
  }, []);

  useEffect(() => {
    void refreshCoverage();
  }, [refreshCoverage]);

  async function handleBackfill() {
    setBackfilling(true);
    setBackfillNote(null);
    try {
      const result = await api.backfillEmbeddings();
      if (!result.enabled) {
        setBackfillNote(
          result.skipped_reason ?? "Embeddings are not enabled on the server.",
        );
      } else {
        setBackfillNote(`Embedded ${result.embedded} of ${result.total} transactions.`);
      }
      await refreshCoverage();
    } catch (e) {
      setBackfillNote(e instanceof Error ? e.message : "Backfill failed");
    } finally {
      setBackfilling(false);
    }
  }

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

        {/* Coverage-aware status banner — explains 0-results BEFORE the user
            gets confused. */}
        <CoverageBanner
          coverage={coverage}
          enabled={enabled}
          backfilling={backfilling}
          note={backfillNote}
          onBackfill={handleBackfill}
        />

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


// ---------------------------------------------------------------------------
// Coverage banner — explains why search may return zero results before the
// user gets frustrated. States:
//   1. pgvector missing on the DB → tell the user (no CTA, server-side fix).
//   2. Coverage 0% → big "Run backfill" CTA.
//   3. Partial coverage → smaller "Top up index" pill.
//   4. 100% — quiet green tick.
// ---------------------------------------------------------------------------

function CoverageBanner({
  coverage,
  enabled,
  backfilling,
  note,
  onBackfill,
}: {
  coverage: EmbeddingCoverageOut | null;
  enabled: boolean | null;
  backfilling: boolean;
  note: string | null;
  onBackfill: () => void;
}) {
  if (coverage === null) return null;

  if (enabled === false) {
    return (
      <div className="rounded-xl bg-amber-50 ring-1 ring-amber-200 text-amber-900 px-4 py-3 text-sm flex items-start gap-3">
        <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
        <div>
          <div className="font-semibold">Semantic search is not enabled.</div>
          <div className="text-amber-800 mt-0.5">
            The database needs the{" "}
            <code className="px-1 rounded bg-amber-100">vector</code>{" "}
            extension. Run{" "}
            <code className="px-1 rounded bg-amber-100">
              CREATE EXTENSION vector;
            </code>{" "}
            on Neon and restart the API, then come back here.
          </div>
        </div>
      </div>
    );
  }

  const { embedded, total, coverage_pct } = coverage;

  if (total === 0) {
    return (
      <div className="rounded-xl bg-ink-50 ring-1 ring-ink-200 text-ink-700 px-4 py-3 text-sm">
        Upload at least one bank statement to populate semantic search.
      </div>
    );
  }

  if (embedded === 0) {
    return (
      <div className="rounded-xl bg-violet-50 ring-1 ring-violet-200 text-violet-900 px-4 py-3 text-sm flex items-start gap-3">
        <Boxes className="h-4 w-4 mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="font-semibold">
            Semantic search isn't ready yet.
          </div>
          <div className="text-violet-800 mt-0.5">
            We need to compute embeddings for your {total} transaction
            {total === 1 ? "" : "s"} first. Takes about{" "}
            {Math.max(1, Math.ceil(total / 100) * 30)} seconds — one click
            and you're done.
          </div>
          {note && <div className="text-xs text-violet-700 mt-2">{note}</div>}
        </div>
        <button
          type="button"
          onClick={onBackfill}
          disabled={backfilling}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 h-8 rounded-lg bg-violet-600 text-white text-xs font-medium hover:bg-violet-700 disabled:opacity-50"
        >
          {backfilling ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Boxes className="h-3.5 w-3.5" />
          )}
          {backfilling ? "Embedding…" : "Run backfill"}
        </button>
      </div>
    );
  }

  if (coverage_pct < 100) {
    return (
      <div className="rounded-xl bg-brand-50 ring-1 ring-brand-200 text-brand-900 px-4 py-3 text-sm flex items-start gap-3">
        <Boxes className="h-4 w-4 mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="font-medium">
            {embedded} of {total} transactions indexed (
            {coverage_pct.toFixed(0)}%).
          </div>
          <div className="text-brand-800 text-xs mt-0.5">
            Newer uploads need embedding before they're searchable here.
          </div>
          {note && <div className="text-xs text-brand-700 mt-1">{note}</div>}
        </div>
        <button
          type="button"
          onClick={onBackfill}
          disabled={backfilling}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 h-8 rounded-lg ring-1 ring-brand-300 bg-white text-brand-700 text-xs font-medium hover:bg-brand-100 disabled:opacity-50"
        >
          {backfilling ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : null}
          Top up index
        </button>
      </div>
    );
  }

  // 100% — quiet success
  return (
    <div className="flex items-center gap-2 text-xs text-emerald-700">
      <CheckCircle2 className="h-3.5 w-3.5" />
      <span>
        All {total} transactions indexed.{note ? ` ${note}` : ""}
      </span>
    </div>
  );
}
