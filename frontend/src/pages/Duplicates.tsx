import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  FileWarning,
  Loader2,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import TopBar from "../components/TopBar";
import SectionCard from "../components/SectionCard";
import EmptyState from "../components/EmptyState";
import { api } from "../api";
import type {
  BackfillHashesOut,
  DuplicateClusterOut,
  DuplicateClustersOut,
  DuplicateDocOut,
} from "../types";
import { formatINRShort } from "../lib/format";
import { cn } from "../lib/cn";

/**
 * Duplicate-review queue.
 *
 * Lists clusters of likely duplicate documents (exact SHA-256 match OR
 * fuzzy financial fingerprint). Lets the user pick a canonical doc per
 * cluster and delete the rest. Soft-deleting removes the doc's bank
 * transactions from the dashboard but keeps the document row for audit.
 */
export default function Duplicates() {
  const [data, setData] = useState<DuplicateClustersOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Which doc IDs are currently being deleted (spinner state).
  const [deleting, setDeleting] = useState<Set<string>>(new Set());

  // Track which doc per cluster the user chose as the canonical "keeper" —
  // the others in the cluster show as deletable. Defaults to the oldest
  // (first in the list, since backend sorts by created_at ASC).
  const [canonical, setCanonical] = useState<Record<string, string>>({});

  const [backfillBusy, setBackfillBusy] = useState(false);
  const [backfillResult, setBackfillResult] = useState<BackfillHashesOut | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.listDuplicates();
      setData(result);
      // Default canonical = first doc in each cluster.
      const defaults: Record<string, string> = {};
      for (const cluster of result.clusters) {
        if (cluster.docs.length > 0) defaults[cluster.cluster_id] = cluster.docs[0].id;
      }
      setCanonical(defaults);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runBackfill = useCallback(async () => {
    setBackfillBusy(true);
    setBackfillResult(null);
    try {
      const result = await api.backfillHashes(500);
      setBackfillResult(result);
      // Re-scan clusters so exact-hash matches surface for any newly hashed docs.
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBackfillBusy(false);
    }
  }, [load]);

  const handleDelete = useCallback(
    async (docId: string) => {
      if (
        !window.confirm(
          "Delete this document as a duplicate?\n\nThis removes its bank transactions from the dashboard. The document row is kept for audit. This cannot be undone from the UI.",
        )
      )
        return;
      setDeleting((s) => new Set(s).add(docId));
      try {
        await api.deleteAsDuplicate(docId);
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setDeleting((s) => {
          const next = new Set(s);
          next.delete(docId);
          return next;
        });
      }
    },
    [load],
  );

  return (
    <>
      <TopBar
        title="Duplicate review"
        subtitle="Documents that look like the same source uploaded more than once"
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={runBackfill}
              disabled={backfillBusy}
              className="btn bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50"
              title="Compute SHA-256 hashes for documents uploaded before the hash column existed"
            >
              {backfillBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" />
              )}
              Backfill hashes
            </button>
            <button
              onClick={() => void load()}
              disabled={loading}
              className="btn bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50"
            >
              <RefreshCw
                className={cn("h-3.5 w-3.5", loading && "animate-spin")}
              />
              Re-scan
            </button>
          </div>
        }
      />

      <div className="p-6 space-y-6">
        {backfillResult && (
          <div className="rounded-xl bg-emerald-50 ring-1 ring-emerald-200 text-emerald-900 p-3 text-sm">
            Backfill done — processed {backfillResult.processed}, hashed{" "}
            {backfillResult.updated}, missing {backfillResult.skipped}, errors{" "}
            {backfillResult.errors}.
          </div>
        )}

        {error && (
          <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 text-rose-900 p-4 text-sm">
            {error}
          </div>
        )}

        {loading && !data && (
          <div className="rounded-xl bg-ink-50 ring-1 ring-ink-200 text-ink-700 p-4 text-sm flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            Scanning your documents for duplicates…
          </div>
        )}

        {!loading && data && (
          <>
            <SummaryBar
              totalClusters={data.total_clusters}
              totalDuplicateDocs={data.total_duplicate_docs}
            />

            {data.clusters.length === 0 ? (
              <EmptyState
                Icon={CheckCircle2}
                title="No duplicates found"
                hint="Your inbox is clean — every uploaded document looks distinct. Re-scan after uploading more to catch anything new."
              />
            ) : (
              <div className="space-y-4">
                {data.clusters.map((cluster) => (
                  <ClusterCard
                    key={cluster.cluster_id}
                    cluster={cluster}
                    canonicalId={canonical[cluster.cluster_id] ?? cluster.docs[0]?.id}
                    onChangeCanonical={(docId) =>
                      setCanonical((c) => ({ ...c, [cluster.cluster_id]: docId }))
                    }
                    onDelete={handleDelete}
                    deleting={deleting}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Summary bar above the cluster list
// ---------------------------------------------------------------------------

function SummaryBar({
  totalClusters,
  totalDuplicateDocs,
}: {
  totalClusters: number;
  totalDuplicateDocs: number;
}) {
  if (totalClusters === 0) return null;
  return (
    <div className="rounded-xl bg-amber-50 ring-1 ring-amber-200 text-amber-900 p-4 text-sm flex items-start gap-2">
      <FileWarning className="h-4 w-4 mt-0.5 shrink-0" />
      <div>
        Found <span className="font-semibold">{totalClusters}</span>{" "}
        {totalClusters === 1 ? "cluster" : "clusters"} containing{" "}
        <span className="font-semibold">{totalDuplicateDocs}</span> redundant{" "}
        {totalDuplicateDocs === 1 ? "document" : "documents"}. Pick which copy
        to keep per cluster and delete the rest — deleted copies are soft-removed
        so the dashboard stops counting their transactions.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cluster card
// ---------------------------------------------------------------------------

interface ClusterCardProps {
  cluster: DuplicateClusterOut;
  canonicalId: string;
  onChangeCanonical: (docId: string) => void;
  onDelete: (docId: string) => void;
  deleting: Set<string>;
}

function ClusterCard({
  cluster,
  canonicalId,
  onChangeCanonical,
  onDelete,
  deleting,
}: ClusterCardProps) {
  return (
    <SectionCard
      title={cluster.signature}
      subtitle={
        cluster.cluster_type === "exact"
          ? "Exact byte-for-byte match"
          : "Same financial fingerprint — likely re-upload of the same source"
      }
      action={
        <span
          className={cn(
            "chip",
            cluster.cluster_type === "exact"
              ? "bg-rose-50 text-rose-700"
              : "bg-amber-50 text-amber-700",
          )}
        >
          {cluster.cluster_type === "exact" ? (
            <AlertTriangle className="h-3 w-3" />
          ) : (
            <FileWarning className="h-3 w-3" />
          )}
          {cluster.docs.length} copies
        </span>
      }
    >
      <ul className="divide-y divide-ink-100 -mx-1">
        {cluster.docs.map((doc) => {
          const isCanonical = doc.id === canonicalId;
          const isDeleting = deleting.has(doc.id);
          return (
            <li
              key={doc.id}
              className={cn(
                "p-3 grid grid-cols-12 gap-3 items-center transition-colors",
                isCanonical ? "bg-emerald-50/40" : "hover:bg-ink-50",
              )}
            >
              <div className="col-span-1 flex justify-center">
                <input
                  type="radio"
                  name={cluster.cluster_id}
                  checked={isCanonical}
                  onChange={() => onChangeCanonical(doc.id)}
                  className="h-4 w-4 accent-emerald-600"
                  title="Mark as canonical (the one to keep)"
                />
              </div>
              <div className="col-span-5">
                <Link
                  to={`/documents/${doc.id}`}
                  className="font-medium text-ink-900 hover:text-brand-700 truncate block"
                  title={doc.original_filename}
                >
                  {doc.original_filename}
                </Link>
                <div className="text-[11px] text-ink-500 mt-0.5">
                  {doc.document_type} · {(doc.file_size_bytes / 1024).toFixed(0)} KB ·
                  uploaded {new Date(doc.uploaded_at).toLocaleString()}
                  {doc.has_hash ? " · hashed" : " · no hash"}
                </div>
              </div>
              <div className="col-span-4 text-xs text-ink-700">
                {doc.min_date && doc.max_date ? (
                  <div>
                    {doc.min_date === doc.max_date
                      ? doc.min_date
                      : `${doc.min_date} → ${doc.max_date}`}
                  </div>
                ) : (
                  <div className="text-ink-400">no date range</div>
                )}
                <div className="tabular-nums">
                  ↓ {formatINRShort(Number(doc.total_debit))} · ↑{" "}
                  {formatINRShort(Number(doc.total_credit))} · {doc.txn_count} txn
                  {doc.txn_count === 1 ? "" : "s"}
                </div>
              </div>
              <div className="col-span-2 flex justify-end">
                {isCanonical ? (
                  <span className="chip bg-emerald-100 text-emerald-800 text-[11px]">
                    <CheckCircle2 className="h-3 w-3" />
                    Keep
                  </span>
                ) : (
                  <button
                    onClick={() => onDelete(doc.id)}
                    disabled={isDeleting}
                    className="btn bg-rose-50 text-rose-700 ring-1 ring-rose-200 hover:bg-rose-100 disabled:opacity-50"
                  >
                    {isDeleting ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                    Delete
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </SectionCard>
  );
}
