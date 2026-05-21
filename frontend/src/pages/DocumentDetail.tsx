import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  CircleAlert,
  FileSpreadsheet,
  FileText,
  Hourglass,
  Image as ImageIcon,
  Loader2,
  Sparkles,
} from "lucide-react";
import TopBar from "../components/TopBar";
import StatusBadge from "../components/StatusBadge";
import Skeleton from "../components/Skeleton";
import { api } from "../api";
import type { DocumentDetailOut, DocumentStatus, FileType } from "../types";
import { formatBytes, timeAgo } from "../lib/format";
import { cn } from "../lib/cn";

const PIPELINE: { key: DocumentStatus; label: string }[] = [
  { key: "received", label: "Received" },
  { key: "extracting", label: "Extracting" },
  { key: "extracted", label: "Extracted" },
  { key: "understood", label: "Understood" },
  { key: "indexed", label: "Indexed" },
];

export default function DocumentDetail() {
  const { id = "" } = useParams();
  const [doc, setDoc] = useState<DocumentDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.getDocument(id);
      setDoc(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Live-update while still being processed.
  useEffect(() => {
    if (!doc) return;
    if (doc.status === "indexed" || doc.status === "error") return;
    const interval = setInterval(load, 1500);
    return () => clearInterval(interval);
  }, [doc, load]);

  return (
    <>
      <TopBar
        title={doc?.original_filename ?? "Document"}
        subtitle={
          doc
            ? `${doc.file_type.toUpperCase()} · ${formatBytes(doc.file_size_bytes)} · ${timeAgo(doc.created_at)}`
            : "Loading…"
        }
        actions={
          <Link to="/inbox" className="btn-ghost">
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to inbox
          </Link>
        }
      />

      <div className="p-6 space-y-6">
        {error && (
          <div className="rounded-xl bg-rose-50 text-rose-700 ring-1 ring-rose-200 p-4 text-sm">
            {error}
          </div>
        )}

        {!doc && !error && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Skeleton className="h-72 lg:col-span-1" />
            <Skeleton className="h-72 lg:col-span-2" />
          </div>
        )}

        {doc && (
          <>
            {/* Pipeline */}
            <section className="card p-5">
              <h3 className="text-sm font-semibold text-ink-900 mb-3">Pipeline</h3>
              <Timeline status={doc.status} />
              {doc.status === "error" && doc.error_message && (
                <div className="mt-4 rounded-lg bg-rose-50 ring-1 ring-rose-200 text-rose-700 p-3 text-xs">
                  {doc.error_message}
                </div>
              )}
            </section>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              {/* File card */}
              <section className="card p-5 lg:col-span-1">
                <h3 className="text-sm font-semibold text-ink-900 mb-3">File</h3>
                <FilePreview type={doc.file_type} name={doc.original_filename} />
                <dl className="mt-4 space-y-2 text-sm">
                  <Row label="Type">{doc.file_type.toUpperCase()}</Row>
                  <Row label="Document type">
                    {doc.document_type === "unknown" ? (
                      <span className="text-ink-500">Awaiting classification</span>
                    ) : (
                      doc.document_type.replace(/_/g, " ")
                    )}
                  </Row>
                  <Row label="Size">{formatBytes(doc.file_size_bytes)}</Row>
                  <Row label="Status">
                    <StatusBadge status={doc.status} />
                  </Row>
                  <Row label="Uploaded">{timeAgo(doc.created_at)}</Row>
                  <Row label="Processed">{timeAgo(doc.processed_at)}</Row>
                  <Row label="Document ID">
                    <span className="font-mono text-[11px] text-ink-500">{doc.id}</span>
                  </Row>
                </dl>
              </section>

              {/* Extraction */}
              <section className="card p-5 lg:col-span-2">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-ink-900 flex items-center gap-2">
                    <Sparkles className="h-3.5 w-3.5 text-brand-600" />
                    Extracted data
                  </h3>
                  {doc.raw_extraction_json?.stub === true && (
                    <span className="chip bg-amber-100 text-amber-800">
                      stub extractor
                    </span>
                  )}
                </div>

                {doc.raw_extraction_json ? (
                  <pre className="text-xs text-ink-800 bg-ink-50 rounded-lg p-4 overflow-auto max-h-[28rem] font-mono leading-relaxed">
                    {JSON.stringify(doc.raw_extraction_json, null, 2)}
                  </pre>
                ) : (
                  <div className="h-48 flex items-center justify-center text-sm text-ink-500 rounded-lg bg-ink-50">
                    Waiting for extraction…
                  </div>
                )}

                <div className="mt-4 text-xs text-ink-500">
                  Real OCR + LLM extraction lands in Week 5–6. Edits made here will be
                  captured as <span className="font-mono">FeedbackEvent</span> rows so the
                  understanding layer can learn over time.
                </div>
              </section>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-ink-500 text-xs uppercase tracking-wide pt-0.5">{label}</dt>
      <dd className="text-ink-900 text-right">{children}</dd>
    </div>
  );
}

function FilePreview({ type, name }: { type: FileType; name: string }) {
  const Icon =
    type === "image"
      ? ImageIcon
      : type === "csv" || type === "xlsx"
        ? FileSpreadsheet
        : FileText;
  return (
    <div className="aspect-[4/3] rounded-xl bg-gradient-to-br from-ink-100 to-ink-200 ring-1 ring-ink-200 flex flex-col items-center justify-center text-ink-600">
      <Icon className="h-10 w-10 mb-2" />
      <div className="text-xs font-mono px-3 truncate max-w-full">{name}</div>
      <div className="text-[10px] text-ink-500 mt-2">Preview coming soon</div>
    </div>
  );
}

function Timeline({ status }: { status: DocumentStatus }) {
  const isError = status === "error";
  const currentIdx = PIPELINE.findIndex((s) => s.key === status);
  return (
    <ol className="flex items-center w-full">
      {PIPELINE.map((step, i) => {
        const isActive = !isError && i === currentIdx;
        const isComplete = !isError && i < currentIdx;
        const isDone = !isError && currentIdx === PIPELINE.length - 1;
        const Icon = isComplete || isDone
          ? Check
          : isActive
            ? Loader2
            : Hourglass;

        return (
          <li
            key={step.key}
            className={cn("flex items-center", i < PIPELINE.length - 1 ? "flex-1" : "")}
          >
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  "h-8 w-8 rounded-full flex items-center justify-center ring-2",
                  isError
                    ? "bg-rose-100 ring-rose-200 text-rose-600"
                    : isComplete || isDone
                      ? "bg-emerald-100 ring-emerald-200 text-emerald-700"
                      : isActive
                        ? "bg-brand-100 ring-brand-200 text-brand-700"
                        : "bg-ink-100 ring-ink-200 text-ink-500",
                )}
              >
                {isError && i === currentIdx ? (
                  <CircleAlert className="h-4 w-4" />
                ) : (
                  <Icon className={cn("h-4 w-4", isActive && "animate-spin")} />
                )}
              </div>
              <div className="text-[11px] mt-1.5 text-ink-700 font-medium">
                {step.label}
              </div>
            </div>
            {i < PIPELINE.length - 1 && (
              <div
                className={cn(
                  "flex-1 h-0.5 mx-2 -mt-5 rounded-full",
                  isError
                    ? "bg-ink-200"
                    : i < currentIdx
                      ? "bg-emerald-300"
                      : "bg-ink-200",
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
