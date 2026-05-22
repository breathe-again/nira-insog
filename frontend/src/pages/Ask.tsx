/** Q&A — "ask anything" over your books.
 *
 * Big text input at the top, a stream of past Q&As below. Each answer
 * shows the plain-English summary, the row count, a sample table of the
 * data the LLM saw, and a collapsible block with the actual SQL that
 * was executed (for transparency / debugging).
 *
 * Conversation history persists in localStorage for the session so the
 * founder can scroll back through earlier questions.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Database,
  Loader2,
  MessageSquare,
  Send,
  Sparkles,
  Trash2,
} from "lucide-react";
import TopBar from "../components/TopBar";
import { api } from "../api";
import type { AskOut } from "../types";
import { cn } from "../lib/cn";

interface QAEntry extends AskOut {
  ts: string;       // ISO timestamp when asked
  error?: string;   // present when the call failed
}

const STORAGE_KEY = "nira:qaHistory";
const MAX_HISTORY = 30;

const SUGGESTIONS = [
  "How much did I spend on AWS last quarter?",
  "Show all payments above ₹1 lakh in FY 25-26",
  "Which vendors did I pay most in April?",
  "How many invoices are still unpaid?",
  "Show transfers to Abhijit this year",
  "What's my total spend on rent in 2025?",
  "Which clients haven't paid me for 60+ days?",
  "What were my top 5 expense categories last month?",
];


export default function Ask() {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<QAEntry[]>(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored) return JSON.parse(stored) as QAEntry[];
    } catch {
      // ignore
    }
    return [];
  });

  // Persist conversation history.
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(0, MAX_HISTORY)));
    } catch {
      // localStorage can be off in some browsers — non-fatal
    }
  }, [history]);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  async function submit(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true);
    setQuestion("");
    try {
      const result = await api.ask(q);
      setHistory((prev) => [
        { ...result, ts: new Date().toISOString() },
        ...prev,
      ]);
    } catch (e) {
      setHistory((prev) => [
        {
          question: q,
          sql: null,
          row_count: 0,
          sample: [],
          answer: "",
          ts: new Date().toISOString(),
          error: e instanceof Error ? e.message : String(e),
        },
        ...prev,
      ]);
    } finally {
      setBusy(false);
      // Scroll the latest answer into view
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({ top: 0, behavior: "smooth" });
      });
    }
  }

  function clearHistory() {
    setHistory([]);
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }

  const hasHistory = history.length > 0;

  return (
    <>
      <TopBar
        title="Ask"
        subtitle="Plain-English questions about your books"
        actions={
          hasHistory && (
            <button
              type="button"
              onClick={clearHistory}
              className="inline-flex items-center gap-1.5 text-xs text-ink-500 hover:text-rose-600 px-2 py-1 rounded-md hover:bg-ink-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Clear history
            </button>
          )
        }
      />

      <div className="p-6 space-y-5">
        {/* Composer */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit(question);
          }}
          className="relative"
        >
          <MessageSquare className="absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 text-ink-400 pointer-events-none" />
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit(question);
              }
            }}
            placeholder="Ask anything — e.g. 'How much did I spend on AWS last quarter?'"
            rows={2}
            disabled={busy}
            className="w-full pl-12 pr-16 py-3 rounded-2xl ring-1 ring-ink-200 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-brand-600 resize-none disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={busy || !question.trim()}
            className="absolute right-3 top-1/2 -translate-y-1/2 inline-flex items-center justify-center h-9 w-9 rounded-xl bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-40"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </form>

        {/* Suggestions (only when empty) */}
        {!hasHistory && (
          <div className="rounded-2xl bg-white ring-1 ring-ink-200 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-900 mb-3">
              <Sparkles className="h-4 w-4 text-brand-600" />
              Try one of these
            </div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void submit(s)}
                  disabled={busy}
                  className="px-3 py-1.5 rounded-lg text-xs text-ink-700 bg-ink-50 ring-1 ring-ink-200 hover:bg-ink-100 disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
            <p className="text-xs text-ink-500 mt-4">
              We send your question to Claude with your DB schema. Claude writes
              a read-only SELECT, we validate it (tenant-locked, no DML), run
              it, and Claude summarizes the result. Every step is visible to you
              in each answer card below.
            </p>
          </div>
        )}

        {/* History */}
        <div ref={scrollRef} className="space-y-4">
          {busy && (
            <div className="rounded-2xl bg-white ring-1 ring-ink-200 p-5 flex items-center gap-3 text-sm text-ink-600">
              <Loader2 className="h-4 w-4 animate-spin text-brand-600" />
              Thinking — writing SQL, running it, and composing an answer…
            </div>
          )}

          {history.map((qa, idx) => (
            <QAResult key={`${qa.ts}-${idx}`} qa={qa} />
          ))}
        </div>
      </div>
    </>
  );
}


// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------


function QAResult({ qa }: { qa: QAEntry }) {
  const [sqlOpen, setSqlOpen] = useState(false);
  const [dataOpen, setDataOpen] = useState(false);

  return (
    <div className="rounded-2xl bg-white ring-1 ring-ink-200 overflow-hidden">
      {/* Question */}
      <div className="px-5 py-3 border-b border-ink-100 bg-ink-50/50">
        <div className="text-[10px] uppercase tracking-wider text-ink-500">
          You asked
        </div>
        <div className="text-sm text-ink-900 mt-0.5">{qa.question}</div>
      </div>

      {/* Answer */}
      <div className="p-5">
        {qa.error ? (
          <div className="text-sm text-rose-700 bg-rose-50 ring-1 ring-rose-200 rounded-lg px-3 py-2">
            {qa.error}
          </div>
        ) : (
          <p className="text-sm text-ink-900 leading-relaxed whitespace-pre-line">
            {qa.answer || "(no answer)"}
          </p>
        )}

        {/* Footer chips */}
        <div className="flex flex-wrap items-center gap-2 mt-4 text-xs">
          {qa.row_count > 0 && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">
              <Database className="h-3 w-3" />
              {qa.row_count} row{qa.row_count === 1 ? "" : "s"}
            </span>
          )}
          {qa.sample.length > 0 && (
            <button
              type="button"
              onClick={() => setDataOpen((v) => !v)}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-ink-600 hover:bg-ink-50"
            >
              {dataOpen ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              Show data
            </button>
          )}
          {qa.sql && (
            <button
              type="button"
              onClick={() => setSqlOpen((v) => !v)}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-ink-600 hover:bg-ink-50"
            >
              {sqlOpen ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              Show SQL
            </button>
          )}
        </div>

        {/* Data sample */}
        {dataOpen && qa.sample.length > 0 && (
          <div className="mt-3 rounded-xl ring-1 ring-ink-200 overflow-x-auto">
            <SampleTable rows={qa.sample} />
          </div>
        )}

        {/* SQL */}
        {sqlOpen && qa.sql && (
          <pre className="mt-3 rounded-xl bg-ink-900 text-ink-100 text-[11px] p-3 overflow-x-auto whitespace-pre">
            <code>{qa.sql}</code>
          </pre>
        )}
      </div>
    </div>
  );
}


function SampleTable({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows.slice(0, 50)) {
      Object.keys(r).forEach((k) => set.add(k));
    }
    return Array.from(set);
  }, [rows]);

  const visible = rows.slice(0, 20);

  return (
    <table className="w-full text-xs">
      <thead className="bg-ink-50 text-ink-600">
        <tr>
          {cols.map((c) => (
            <th key={c} className="px-2 py-1.5 text-left font-medium">
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {visible.map((r, i) => (
          <tr key={i} className={cn(i % 2 === 0 ? "bg-white" : "bg-ink-50/40")}>
            {cols.map((c) => (
              <td key={c} className="px-2 py-1.5 text-ink-800 align-top">
                {formatCell(r[c])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
      {rows.length > visible.length && (
        <tfoot>
          <tr>
            <td
              colSpan={cols.length}
              className="px-2 py-1.5 text-ink-500 italic"
            >
              … {rows.length - visible.length} more rows
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  );
}


function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") {
    return v.length > 60 ? v.slice(0, 60) + "…" : v;
  }
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}
