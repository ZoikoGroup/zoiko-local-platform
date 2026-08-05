"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { listProviderStatuses, ApiError, type ProviderStatus } from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const PROVIDER_LABELS: Record<string, string> = {
  twilio: "Twilio (telecom)",
  livekit: "LiveKit (video)",
  groq: "Groq (AI transcription/summaries)",
  stripe_identity: "Stripe Identity (KYC/KYB)",
  resend: "Resend (email)",
  storage_s3: "S3-compatible storage (recordings)",
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
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
            </div>
            <span className={`text-xs font-medium rounded-full px-2.5 py-1 shrink-0 ${statusStyle(p.configured, p.ok)}`}>
              {statusLabel(p.configured, p.ok)}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
