/** Formatting helpers — currency, dates, sizes, etc. */

import { formatDistanceToNowStrict } from "date-fns";

/** Format INR currency. e.g. 4230000 → "₹42.30L" (Indian-style lakh/crore). */
export function formatINRShort(amount: number): string {
  const sign = amount < 0 ? "-" : "";
  const abs = Math.abs(amount);
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)}L`;
  if (abs >= 1000) return `${sign}₹${(abs / 1000).toFixed(1)}K`;
  return `${sign}₹${abs.toFixed(0)}`;
}

/** Full INR with thousand separators (Indian-style). */
export function formatINR(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(amount);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return formatDistanceToNowStrict(new Date(iso), { addSuffix: true });
  } catch {
    return "—";
  }
}

export function percent(n: number): string {
  return `${(n * 100).toFixed(0)}%`;
}

export function deltaLabel(curr: number, prev: number): {
  pct: number;
  label: string;
  dir: "up" | "down" | "flat";
} {
  if (prev === 0) return { pct: 0, label: "—", dir: "flat" };
  const pct = (curr - prev) / prev;
  const dir = pct > 0.01 ? "up" : pct < -0.01 ? "down" : "flat";
  const label = `${pct > 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`;
  return { pct, label, dir };
}
