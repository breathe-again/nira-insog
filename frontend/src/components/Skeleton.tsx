import { cn } from "../lib/cn";

export default function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse-soft rounded-md bg-ink-200/60", className)} />;
}
