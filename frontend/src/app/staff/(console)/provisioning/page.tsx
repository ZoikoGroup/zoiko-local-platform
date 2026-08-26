"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  listStuckProvisioning,
  retryProvisioning,
  releaseProvisioning,
  ApiError,
  type StuckProvisioningEntry,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

export default function StaffProvisioningPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [entries, setEntries] = useState<StuckProvisioningEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<Record<string, string>>({});

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const loadEntries = useCallback(() => {
    if (!token) return;
    return listStuckProvisioning(token)
      .then((data) => {
        setEntries(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load the provisioning recovery queue.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    loadEntries();
  }, [loadEntries]);

  async function handleRetry(numberId: string) {
    if (!token) return;
    setBusyId(numberId);
    setActionError((prev) => ({ ...prev, [numberId]: "" }));
    try {
      await retryProvisioning(token, numberId);
      await loadEntries();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Retry failed.";
      setActionError((prev) => ({ ...prev, [numberId]: message }));
    } finally {
      setBusyId(null);
    }
  }

  async function handleRelease(numberId: string) {
    if (!token) return;
    setBusyId(numberId);
    setActionError((prev) => ({ ...prev, [numberId]: "" }));
    try {
      await releaseProvisioning(token, numberId);
      await loadEntries();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Couldn't release this number.";
      setActionError((prev) => ({ ...prev, [numberId]: message }));
    } finally {
      setBusyId(null);
    }
  }

  if (!token) return null;

  return (
    <>
      <p className="text-xs text-slate-400 bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">
        Number purchases run synchronously - a row still showing here means the backend crashed or restarted
        mid-purchase, not that anything is slow. Retry re-attempts the provider purchase; Release just frees
        the number back to Reserved without contacting the provider again.
      </p>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {!loading && entries.length === 0 && (
        <p className="text-sm text-slate-400">Nothing stuck right now.</p>
      )}

      <div className="space-y-3">
        {entries.map((e) => (
          <div key={e.id} className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-mono font-medium text-white">{e.e164}</div>
                <div className="text-xs text-slate-400">
                  {e.account_name ?? "Unknown account"} &middot; {e.account_owner_email ?? "—"}
                </div>
              </div>
              <span className="text-xs font-medium rounded-full px-2.5 py-1 capitalize bg-amber-950 text-amber-400">
                {e.status.replaceAll("_", " ")}
              </span>
            </div>

            <div className="mt-3 text-sm text-slate-400">
              Stuck since{" "}
              {e.provisioning_started_at ? new Date(e.provisioning_started_at).toLocaleString() : "unknown"}
            </div>

            {actionError[e.id] && <p className="mt-2 text-sm text-red-400">{actionError[e.id]}</p>}

            <div className="mt-4 pt-4 border-t border-slate-800 flex gap-2">
              <button
                onClick={() => handleRetry(e.id)}
                disabled={busyId === e.id}
                className="text-sm bg-emerald-700 hover:bg-emerald-600 disabled:opacity-60 text-white rounded-lg px-4 py-1.5 transition"
              >
                {busyId === e.id ? "Working..." : "Retry purchase"}
              </button>
              <button
                onClick={() => handleRelease(e.id)}
                disabled={busyId === e.id}
                className="text-sm bg-slate-800 hover:bg-slate-700 disabled:opacity-60 text-white rounded-lg px-4 py-1.5 transition"
              >
                Release to Reserved
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
