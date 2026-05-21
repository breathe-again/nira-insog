import { useEffect, useState } from "react";
import { Activity, Database, Globe, ServerCog } from "lucide-react";
import TopBar from "../components/TopBar";
import SectionCard from "../components/SectionCard";
import { api, API_BASE } from "../api";
import type { ApiHealth } from "../types";
import { cn } from "../lib/cn";

const SERVICE_META: Record<string, { label: string; Icon: typeof Database }> = {
  postgres: { label: "Postgres", Icon: Database },
  redis: { label: "Redis", Icon: ServerCog },
  api: { label: "API", Icon: Globe },
};

export default function Health() {
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastCheck, setLastCheck] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await api.health();
        if (!cancelled) {
          setHealth(data);
          setError(null);
          setLastCheck(new Date());
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLastCheck(new Date());
        }
      }
    }
    void load();
    const id = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const allOk = health?.status === "ok" && !error;

  return (
    <>
      <TopBar
        title="System"
        subtitle={`Polling ${API_BASE}/api/health every 5s`}
        actions={
          <span
            className={cn(
              "chip ring-1",
              allOk
                ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                : "bg-amber-50 text-amber-800 ring-amber-200",
            )}
          >
            <Activity className="h-3 w-3" />
            {allOk ? "All systems operational" : "Degraded"}
          </span>
        }
      />

      <div className="p-6 grid grid-cols-1 lg:grid-cols-3 gap-4">
        <SectionCard title="Services" subtitle="Backend dependencies" className="lg:col-span-2">
          {error && (
            <div className="rounded-lg bg-rose-50 text-rose-700 ring-1 ring-rose-200 p-3 text-sm mb-3">
              Could not reach API: {error}
            </div>
          )}

          {!error && !health && (
            <div className="text-sm text-ink-500">Checking…</div>
          )}

          {health && (
            <ul className="divide-y divide-ink-100">
              {Object.entries(health.checks).map(([k, v]) => {
                const ok = v === "ok";
                const meta = SERVICE_META[k] ?? { label: k, Icon: ServerCog };
                return (
                  <li key={k} className="flex items-center gap-3 py-3">
                    <div
                      className={cn(
                        "h-9 w-9 rounded-lg flex items-center justify-center",
                        ok ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800",
                      )}
                    >
                      <meta.Icon className="h-4 w-4" />
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-medium text-ink-900">{meta.label}</div>
                      <div className="text-xs text-ink-500 font-mono">{v}</div>
                    </div>
                    <span
                      className={cn(
                        "chip ring-1",
                        ok
                          ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                          : "bg-amber-50 text-amber-800 ring-amber-200",
                      )}
                    >
                      <span
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          ok ? "bg-emerald-500" : "bg-amber-500",
                        )}
                      />
                      {ok ? "Online" : "Issue"}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </SectionCard>

        <SectionCard title="Build info" subtitle="What you are running">
          <ul className="space-y-2 text-sm">
            <Row label="API base">
              <span className="font-mono text-xs text-ink-700">{API_BASE}</span>
            </Row>
            <Row label="Frontend">
              <span className="font-mono text-xs text-ink-700">v0.1.0 · React 18 + Vite</span>
            </Row>
            <Row label="Backend">
              <span className="font-mono text-xs text-ink-700">FastAPI · Celery · Postgres 16</span>
            </Row>
            <Row label="Last check">
              <span className="text-xs text-ink-600">
                {lastCheck ? lastCheck.toLocaleTimeString() : "—"}
              </span>
            </Row>
          </ul>
        </SectionCard>
      </div>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <li className="flex items-center justify-between gap-3">
      <span className="text-ink-500 text-xs uppercase tracking-wide">{label}</span>
      <span className="text-right">{children}</span>
    </li>
  );
}
