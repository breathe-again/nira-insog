import { Check, CircleAlert, Hourglass, Loader2, Sparkles } from "lucide-react";
import type { DocumentStatus } from "../types";
import { cn } from "../lib/cn";

const META: Record<
  DocumentStatus,
  { label: string; cls: string; Icon: React.ComponentType<{ className?: string }> }
> = {
  received: { label: "Received", cls: "bg-ink-100 text-ink-700", Icon: Hourglass },
  extracting: { label: "Extracting", cls: "bg-amber-100 text-amber-800", Icon: Loader2 },
  extracted: { label: "Extracted", cls: "bg-sky-100 text-sky-800", Icon: Sparkles },
  understood: { label: "Understood", cls: "bg-violet-100 text-violet-800", Icon: Sparkles },
  indexed: { label: "Indexed", cls: "bg-emerald-100 text-emerald-800", Icon: Check },
  error: { label: "Error", cls: "bg-red-100 text-red-700", Icon: CircleAlert },
};

export default function StatusBadge({ status }: { status: DocumentStatus }) {
  const m = META[status] ?? META.received;
  const spin = status === "extracting";
  return (
    <span className={cn("chip", m.cls)}>
      <m.Icon className={cn("h-3 w-3", spin && "animate-spin")} />
      {m.label}
    </span>
  );
}
