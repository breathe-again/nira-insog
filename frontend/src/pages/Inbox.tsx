import { useCallback, useEffect, useMemo, useState } from "react";
import { Filter, Search, RefreshCw } from "lucide-react";
import TopBar from "../components/TopBar";
import UploadDropzone from "../components/UploadDropzone";
import DocumentList from "../components/DocumentList";
import { api } from "../api";
import type { DocumentOut, DocumentStatus, DocumentType } from "../types";
import { cn } from "../lib/cn";

const STATUS_FILTERS: { label: string; value: "all" | DocumentStatus }[] = [
  { label: "All", value: "all" },
  { label: "In progress", value: "extracting" },
  { label: "Indexed", value: "indexed" },
  { label: "Errors", value: "error" },
];

export default function Inbox() {
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<"all" | DocumentStatus>("all");
  const [typeFilter, setTypeFilter] = useState<"all" | DocumentType>("all");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await api.listDocuments({ limit: 200 });
      setDocs(data.items);
      setTotal(data.total);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll faster when there are in-flight docs.
  useEffect(() => {
    const anyInFlight = docs.some(
      (d) => d.status !== "indexed" && d.status !== "error",
    );
    const interval = anyInFlight ? 2000 : 8000;
    const id = setInterval(load, interval);
    return () => clearInterval(id);
  }, [docs, load]);

  const onUploaded = useCallback((doc: DocumentOut) => {
    setDocs((prev) => [doc, ...prev]);
    setTotal((t) => t + 1);
  }, []);

  const filtered = useMemo(() => {
    return docs.filter((d) => {
      if (statusFilter !== "all") {
        // Map "extracting" filter to anything in flight.
        const inFlight = ["received", "extracting", "extracted", "understood"];
        if (statusFilter === "extracting") {
          if (!inFlight.includes(d.status)) return false;
        } else if (d.status !== statusFilter) return false;
      }
      if (typeFilter !== "all" && d.document_type !== typeFilter) return false;
      if (query.trim()) {
        const q = query.toLowerCase();
        if (!d.original_filename.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [docs, statusFilter, typeFilter, query]);

  const inFlightCount = docs.filter(
    (d) => d.status !== "indexed" && d.status !== "error",
  ).length;

  return (
    <>
      <TopBar
        title="Inbox"
        subtitle={`${total} document${total === 1 ? "" : "s"} · ${inFlightCount} processing`}
        actions={
          <button onClick={load} className="btn-ghost">
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        }
      />

      <div className="p-6 space-y-6">
        <UploadDropzone onUploaded={onUploaded} />

        {error && (
          <div className="rounded-xl bg-rose-50 text-rose-700 ring-1 ring-rose-200 p-4 text-sm">
            {error}
          </div>
        )}

        {/* Filters */}
        <div className="card p-3 flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded-lg bg-ink-100 p-0.5">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => setStatusFilter(f.value)}
                className={cn(
                  "px-3 py-1.5 rounded-md text-xs font-medium transition-colors",
                  statusFilter === f.value
                    ? "bg-white text-ink-900 shadow-card"
                    : "text-ink-600 hover:text-ink-900",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="hidden sm:flex items-center gap-1 text-xs text-ink-500 ml-2">
            <Filter className="h-3.5 w-3.5" />
            Type:
          </div>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as typeof typeFilter)}
            className="text-sm rounded-lg ring-1 ring-ink-200 bg-white px-2.5 py-1.5 text-ink-700"
          >
            <option value="all">All types</option>
            <option value="bank_statement">Bank statement</option>
            <option value="sales_invoice">Sales invoice</option>
            <option value="purchase_invoice">Purchase invoice</option>
            <option value="receipt">Receipt</option>
            <option value="unknown">Unknown</option>
          </select>

          <div className="flex-1" />

          <div className="flex items-center gap-2 px-3 h-9 rounded-lg ring-1 ring-ink-200 bg-white text-sm">
            <Search className="h-4 w-4 text-ink-400" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search filenames…"
              className="bg-transparent outline-none w-48 text-ink-800"
            />
          </div>
        </div>

        <DocumentList
          documents={filtered}
          loading={loading}
          emptyTitle={
            docs.length === 0 ? "No documents yet" : "No documents match your filters"
          }
          emptyDescription={
            docs.length === 0
              ? "Drop a file above to get started."
              : "Try clearing search or changing the filter."
          }
        />
      </div>
    </>
  );
}
