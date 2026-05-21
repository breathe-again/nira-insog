import { useCallback, useRef, useState } from "react";
import { CheckCircle2, CloudUpload, Loader2 } from "lucide-react";
import { api } from "../api";
import type { DocumentOut } from "../types";
import { cn } from "../lib/cn";

interface Props {
  onUploaded?: (doc: DocumentOut) => void;
}

export default function UploadDropzone({ onUploaded }: Props) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      setBusy(true);
      setError(null);
      const total = files.length;
      setProgress({ done: 0, total });
      try {
        let done = 0;
        for (const file of Array.from(files)) {
          const doc = await api.uploadDocument(file);
          onUploaded?.(doc);
          done++;
          setProgress({ done, total });
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
        setTimeout(() => setProgress(null), 1500);
      }
    },
    [onUploaded],
  );

  return (
    <label
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        void handleFiles(e.dataTransfer.files);
      }}
      className={cn(
        "flex flex-col items-center justify-center w-full rounded-2xl border-2 border-dashed p-10 cursor-pointer transition-all",
        dragging
          ? "border-brand-600 bg-brand-50 scale-[1.01]"
          : "border-ink-300 bg-white hover:border-ink-400 hover:bg-ink-50",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        accept=".pdf,.png,.jpg,.jpeg,.webp,.csv,.xlsx,.xls"
        onChange={(e) => void handleFiles(e.target.files)}
      />

      <div
        className={cn(
          "h-12 w-12 rounded-2xl flex items-center justify-center mb-3 transition-colors",
          dragging ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-500",
        )}
      >
        {busy ? (
          <Loader2 className="h-5 w-5 animate-spin" />
        ) : progress && progress.done === progress.total && progress.total > 0 ? (
          <CheckCircle2 className="h-5 w-5 text-emerald-600" />
        ) : (
          <CloudUpload className="h-5 w-5" />
        )}
      </div>

      <div className="text-sm text-ink-700">
        <span className="font-medium text-brand-700">Click to upload</span> or drag and drop
      </div>
      <div className="text-xs text-ink-500 mt-1">
        PDF · image · CSV · Excel · up to 25 MB
      </div>

      {progress && (
        <div className="mt-4 text-xs text-ink-600 tabular">
          {progress.done} of {progress.total} uploaded
        </div>
      )}

      {error && (
        <div className="mt-4 text-xs text-rose-600 max-w-md text-center">{error}</div>
      )}
    </label>
  );
}
