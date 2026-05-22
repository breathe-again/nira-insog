import { useEffect, useState } from "react";
import { TrendingUp, TrendingDown, PiggyBank } from "lucide-react";
import SectionCard from "./SectionCard";
import { api } from "../api";
import type { InvestmentActivityOut } from "../types";
import { formatINRShort } from "../lib/format";

interface Props {
  from?: string;
  to?: string;
  rangeLabel: string;
}

/**
 * Investment activity widget.
 *
 * Shows three headline numbers — money invested (debits classified as
 * Investments), money redeemed (credits in the same category), and the net.
 * Below the headline a horizontal stack lists the top schemes/AMCs with
 * mini in/out bars.
 *
 * Backend deduplicates Gross/Stamp/Net MF triplets before summing, so the
 * "invested" number here matches what actually moved out of the bank.
 *
 * Renders nothing when the org has no investment activity in the window
 * (keeps the dashboard clean for tenants without fund flows).
 */
export default function InvestmentActivityCard({ from, to, rangeLabel }: Props) {
  const [data, setData] = useState<InvestmentActivityOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .investmentActivity({ from, to })
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [from, to]);

  if (loading || error) return null;
  if (!data) return null;

  const invested = Number(data.invested_total);
  const redeemed = Number(data.redeemed_total);
  const net = Number(data.net_invested);

  // Nothing happened in this window — skip the widget entirely.
  if (invested <= 0 && redeemed <= 0) return null;

  // For the mini bars, scale each scheme's invested + redeemed against the
  // largest combined movement so the longest bar uses 100% of its track.
  const maxMovement = data.by_scheme.reduce(
    (m, s) => Math.max(m, Number(s.invested) + Number(s.redeemed)),
    1,
  );

  return (
    <SectionCard
      title="Investment activity"
      subtitle={`${rangeLabel} · mutual funds, bonds, equities`}
      action={
        <span className="chip bg-teal-50 text-teal-700">
          <PiggyBank className="h-3 w-3" />
          {data.txn_count_in + data.txn_count_out} txns
        </span>
      }
    >
      {/* Three headline numbers */}
      <div className="grid grid-cols-3 gap-2 mb-4">
        <HeadlineTile
          label="Invested"
          value={formatINRShort(invested)}
          hint={`${data.txn_count_in} buys`}
          tone="out"
          icon={<TrendingDown className="h-4 w-4" />}
        />
        <HeadlineTile
          label="Redeemed"
          value={formatINRShort(redeemed)}
          hint={`${data.txn_count_out} sells`}
          tone="in"
          icon={<TrendingUp className="h-4 w-4" />}
        />
        <HeadlineTile
          label="Net invested"
          value={formatINRShort(Math.abs(net))}
          hint={net >= 0 ? "money in" : "money out"}
          tone={net >= 0 ? "net-in" : "net-out"}
        />
      </div>

      {/* Scheme breakdown — top contributors */}
      {data.by_scheme.length > 0 ? (
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500 mb-2">
            By scheme
          </div>
          <ul className="space-y-2">
            {data.by_scheme.slice(0, 8).map((s) => (
              <SchemeRow
                key={s.scheme}
                scheme={s.scheme}
                invested={Number(s.invested)}
                redeemed={Number(s.redeemed)}
                txnCount={s.txn_count}
                maxMovement={maxMovement}
              />
            ))}
          </ul>
        </div>
      ) : (
        <div className="text-xs text-ink-500">
          No scheme breakdown available — descriptions didn't contain
          recognizable AMC names.
        </div>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Headline tile
// ---------------------------------------------------------------------------

interface HeadlineTileProps {
  label: string;
  value: string;
  hint: string;
  tone: "in" | "out" | "net-in" | "net-out";
  icon?: React.ReactNode;
}

function HeadlineTile({ label, value, hint, tone, icon }: HeadlineTileProps) {
  const palette = {
    out: "bg-rose-50 text-rose-900 ring-rose-200",
    in: "bg-emerald-50 text-emerald-900 ring-emerald-200",
    "net-in": "bg-teal-50 text-teal-900 ring-teal-200",
    "net-out": "bg-amber-50 text-amber-900 ring-amber-200",
  }[tone];

  return (
    <div className={`rounded-xl ring-1 p-3 ${palette}`}>
      <div className="flex items-center gap-1.5 text-[11px] font-medium opacity-80">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
      <div className="text-[11px] opacity-70 mt-0.5">{hint}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scheme row — name on the left, mini in/out bars on the right
// ---------------------------------------------------------------------------

interface SchemeRowProps {
  scheme: string;
  invested: number;
  redeemed: number;
  txnCount: number;
  maxMovement: number;
}

function SchemeRow({
  scheme,
  invested,
  redeemed,
  txnCount,
  maxMovement,
}: SchemeRowProps) {
  const total = invested + redeemed;
  const investedPct = maxMovement > 0 ? (invested / maxMovement) * 100 : 0;
  const redeemedPct = maxMovement > 0 ? (redeemed / maxMovement) * 100 : 0;
  const net = invested - redeemed;

  return (
    <li className="grid grid-cols-12 gap-2 items-center text-xs">
      <div className="col-span-4 truncate font-medium text-ink-800" title={scheme}>
        {scheme}
      </div>
      <div className="col-span-5">
        <div className="flex items-center gap-0.5 h-2 rounded-full overflow-hidden bg-ink-100">
          {investedPct > 0 && (
            <div
              className="h-full bg-rose-400"
              style={{ width: `${investedPct}%` }}
              title={`Invested: ${formatINRShort(invested)}`}
            />
          )}
          {redeemedPct > 0 && (
            <div
              className="h-full bg-emerald-400"
              style={{ width: `${redeemedPct}%` }}
              title={`Redeemed: ${formatINRShort(redeemed)}`}
            />
          )}
        </div>
      </div>
      <div className="col-span-3 text-right tabular-nums">
        <div className="text-ink-800">
          {net >= 0 ? "" : "+"}
          {formatINRShort(Math.abs(total))}
        </div>
        <div className="text-[10px] text-ink-500">
          {txnCount} {txnCount === 1 ? "txn" : "txns"}
        </div>
      </div>
    </li>
  );
}
