import { FileSpreadsheet, FileText, Image as ImageIcon, Files } from "lucide-react";
import { Link } from "react-router-dom";
import type { DocumentOut, FileType, DocumentType } from "../types";
import StatusBadge from "./StatusBadge";
import { formatBytes, timeAgo } from "../lib/format";

const ICON_FOR: Record<FileType, typeof FileText> = {
  pdf: FileText,
  image: ImageIcon,
  csv: FileSpreadsheet,
  xlsx: FileSpreadsheet,
};

const TYPE_LABEL: Record<DocumentType, string> = {
  bank_statement: "Bank statement",
  sales_invoice: "Sales invoice",
  purchase_invoice: "Purchase invoice",
  receipt: "Receipt",
  unknown: "—",
};

interface Props {
  documents: DocumentOut[];
  loading?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
}

export default function DocumentList({
  documents,
  loading,
  emptyTitle = "No documents yet",
  emptyDescription = "Drop a file above to get started.",
}: Props) {
  if (loading && documents.length === 0) {
    return (
      <div className="card p-8 text-sm text-ink-500 text-center">Loading…</div>
    );
  }
  if (documents.length === 0) {
    return (
      <div className="card p-10 text-center">
        <div className="mx-auto h-12 w-12 rounded-2xl bg-ink-100 text-ink-500 flex items-center justify-center mb-3">
          <Files className="h-5 w-5" />
        </div>
        <h4 className="text-sm font-semibold text-ink-900">{emptyTitle}</h4>
        <p className="text-sm text-ink-500 mt-1">{emptyDescription}</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <table className="min-w-full text-sm">
        <thead className="bg-ink-50/60 text-ink-600 border-b border-ink-100">
          <tr>
            <Th className="w-1/3">File</Th>
            <Th>Type</Th>
            <Th>Status</Th>
            <Th className="text-right">Size</Th>
            <Th>Uploaded</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {documents.map((d) => {
            const Icon = ICON_FOR[d.file_type] ?? FileText;
            return (
              <tr
                key={d.id}
                className="hover:bg-ink-50/60 transition-colors"
              >
                <Td>
                  <Link
                    to={`/inbox/${d.id}`}
                    className="flex items-center gap-3 group"
                  >
                    <div className="h-9 w-9 rounded-lg bg-ink-100 text-ink-600 flex items-center justify-center shrink-0 group-hover:bg-brand-50 group-hover:text-brand-700 transition-colors">
                      <Icon className="h-4 w-4" />
                    </div>
                    <div className="min-w-0">
                      <div className="font-medium text-ink-900 truncate max-w-[20rem] group-hover:text-brand-700">
                        {d.original_filename}
                      </div>
                      <div className="text-xs text-ink-500 font-mono uppercase">
                        {d.file_type}
                      </div>
                    </div>
                  </Link>
                </Td>
                <Td>
                  <span className="text-ink-700">{TYPE_LABEL[d.document_type]}</span>
                </Td>
                <Td>
                  <StatusBadge status={d.status} />
                  {d.error_message && (
                    <div
                      className="text-xs text-rose-600 mt-1 truncate max-w-[18rem]"
                      title={d.error_message}
                    >
                      {d.error_message}
                    </div>
                  )}
                </Td>
                <Td className="text-right">
                  <span className="text-ink-600 tabular">{formatBytes(d.file_size_bytes)}</span>
                </Td>
                <Td>
                  <span className="text-ink-600 tabular">{timeAgo(d.created_at)}</span>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Th({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      className={
        "px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide " +
        (className ?? "")
      }
    >
      {children}
    </th>
  );
}

function Td({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <td className={"px-4 py-3 align-top " + (className ?? "")}>{children}</td>;
}
