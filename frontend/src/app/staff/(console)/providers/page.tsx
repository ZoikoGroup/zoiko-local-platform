"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  listProviderStatuses,
  listProviderTraces,
  getProviderTraceSummary,
  ApiError,
  type ProviderStatus,
  type ProviderCallTrace,
  type ProviderLatencySummary,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";
import { useStaffRole } from "@/lib/staffRole";

const PROVIDER_LABELS: Record<string, string> = {
  twilio: "Twilio (telecom)",
  livekit: "LiveKit (video)",
  groq: "Groq (AI transcription/summaries)",
  groq_llm: "Groq (LLM)",
  groq_transcription: "Groq (transcription)",
  stripe_identity: "Stripe Identity (KYC/KYB)",
  stripe_payments: "Stripe Payments (number purchase)",
  resend: "Resend (email)",
  storage_s3: "S3-compatible storage (recordings)",
  s3: "S3-compatible storage (recordings)",
  cohere: "Cohere (embeddings)",
};

function statusStyle(configured: boolean, ok: boolean): string {
  if (!configured) return "bg-slate-800 text-slate-400";
  return ok ? "bg-emerald-950/60 text-emerald-400 border border-emerald-900" : "bg-red-950/60 text-red-400 border border-red-900";
}

function statusLabel(configured: boolean, ok: boolean): string {
  if (!configured) return "Not configured";
  return ok ? "Healthy" : "Unreachable";
}

export default function StaffProvidersPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const { role } = useStaffRole();
  const isSuperAdmin = role === "super_admin";
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [summary, setSummary] = useState<ProviderLatencySummary[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [requestIdFilter, setRequestIdFilter] = useState("");
  const [traces, setTraces] = useState<ProviderCallTrace[] | null>(null);
  const [tracesLoading, setTracesLoading] = useState(false);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;
    return listProviderStatuses(token)
      .then((data) => {
        setProviders(data.providers);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load provider status.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  const loadSummary = useCallback(() => {
    if (!token) return;
    return getProviderTraceSummary(token, 24)
      .then(setSummary)
      .catch(() => {
        // Non-fatal - the provider status cards above still work without this.
      })
      .finally(() => setSummaryLoading(false));
  }, [token]);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  function handleLookupRequestId(e: FormEvent) {
    e.preventDefault();
    if (!token || !requestIdFilter.trim()) return;
    setTracesLoading(true);
    listProviderTraces(token, { requestId: requestIdFilter.trim() })
      .then(setTraces)
      .catch(() => setTraces([]))
      .finally(() => setTracesLoading(false));
  }

  if (!token) return null;

  return (
    <>
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-400">
          Live reachability checks against each real provider — not just &ldquo;is a key configured.&rdquo;
        </p>
        <button onClick={load} className="text-xs text-slate-400 hover:text-white transition">
          Refresh
        </button>
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      <div className="space-y-2">
        {providers.map((p) => (
          <div
            key={p.name}
            className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 flex items-center justify-between gap-4"
          >
            <div>
              <div className="text-sm text-white font-medium">{PROVIDER_LABELS[p.name] ?? p.name}</div>
              {p.detail && <div className="text-xs text-slate-400 mt-0.5">{p.detail}</div>}
              {/* Circuit-breaker/failover state - resilience-config detail
                  that matters to whoever's accountable for provider
                  outages, not a routine "is it up" lookup. */}
              {isSuperAdmin && p.circuit_state && (
                <div className="text-[10.5px] font-mono text-slate-600 mt-1">
                  circuit:{p.circuit_state}
                  {p.failover_enabled !== undefined && ` · failover:${p.failover_enabled ? "on" : "off"}`}
                </div>
              )}
            </div>
            <span className={`text-xs font-medium rounded-full px-2.5 py-1 shrink-0 ${statusStyle(p.configured, p.ok)}`}>
              {statusLabel(p.configured, p.ok)}
            </span>
          </div>
        ))}
      </div>

      <div className="pt-6 border-t border-slate-800 space-y-3">
        <div>
          <h3 className="text-sm font-semibold text-white">Call Latency (last 24h)</h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Self-hosted distributed tracing — every outbound provider call, grouped by operation.
          </p>
        </div>

        {summaryLoading && <p className="text-sm text-slate-400">Loading...</p>}
        {!summaryLoading && summary.length === 0 && (
          <p className="text-sm text-slate-400">No provider calls traced in the last 24 hours.</p>
        )}

        {summary.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400 border-b border-slate-800">
                  <th className="pb-2 pr-4 font-medium">Provider</th>
                  <th className="pb-2 pr-4 font-medium">Operation</th>
                  <th className="pb-2 pr-4 font-medium">Calls</th>
                  <th className="pb-2 pr-4 font-medium">Avg</th>
                  <th className="pb-2 pr-4 font-medium">Max</th>
                  <th className="pb-2 font-medium">Failures</th>
                </tr>
              </thead>
              <tbody>
                {summary.map((row) => (
                  <tr key={`${row.provider}:${row.operation}`} className="border-b border-slate-900">
                    <td className="py-2 pr-4 text-slate-200">{PROVIDER_LABELS[row.provider] ?? row.provider}</td>
                    <td className="py-2 pr-4 text-slate-400 font-mono text-xs">{row.operation}</td>
                    <td className="py-2 pr-4 text-slate-300">{row.count}</td>
                    <td className="py-2 pr-4 text-slate-300">{Math.round(row.avg_duration_ms)}ms</td>
                    <td className="py-2 pr-4 text-slate-300">{Math.round(row.max_duration_ms)}ms</td>
                    <td className={`py-2 ${row.failure_count > 0 ? "text-red-400 font-medium" : "text-slate-500"}`}>
                      {row.failure_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="pt-6 border-t border-slate-800 space-y-3">
        <div>
          <h3 className="text-sm font-semibold text-white">Trace a request</h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Paste a request ID (from the X-Request-ID response header, or an error report) to see every
            outbound provider call it made.
          </p>
        </div>

        <form onSubmit={handleLookupRequestId} className="flex items-center gap-2">
          <input
            value={requestIdFilter}
            onChange={(e) => setRequestIdFilter(e.target.value)}
            placeholder="request id"
            className="flex-1 max-w-sm text-sm rounded-lg bg-slate-900 border border-slate-800 text-white px-3 py-1.5 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-600"
          />
          <button
            type="submit"
            disabled={!requestIdFilter.trim() || tracesLoading}
            className="text-xs font-medium rounded-lg px-3 py-1.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-white"
          >
            {tracesLoading ? "Looking up..." : "Look up"}
          </button>
        </form>

        {traces !== null && (
          <ul className="space-y-1.5">
            {traces.length === 0 ? (
              <p className="text-sm text-slate-400">No provider calls found for that request id.</p>
            ) : (
              traces.map((t) => (
                <li
                  key={t.id}
                  className="flex items-center justify-between gap-3 text-sm bg-slate-900 border border-slate-800 rounded-lg px-3 py-2"
                >
                  <span className="text-slate-200">
                    {PROVIDER_LABELS[t.provider] ?? t.provider}
                    <span className="text-slate-500 font-mono text-xs ml-2">{t.operation}</span>
                  </span>
                  <span className="flex items-center gap-3 shrink-0 text-xs">
                    <span className="text-slate-400">{Math.round(t.duration_ms)}ms</span>
                    <span className={t.success ? "text-emerald-400" : "text-red-400"}>
                      {t.success ? "ok" : t.error_detail ?? "failed"}
                    </span>
                  </span>
                </li>
              ))
            )}
          </ul>
        )}
      </div>
    </>
  );
}
