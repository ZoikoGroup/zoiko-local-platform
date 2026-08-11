"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  listBlockedDestinations,
  addBlockedDestination,
  removeBlockedDestination,
  listFraudCases,
  resolveFraudCase,
  ApiError,
  type BlockedDestination,
  type FraudCase,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

export default function StaffRiskPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();

  const [destinations, setDestinations] = useState<BlockedDestination[]>([]);
  const [prefix, setPrefix] = useState("");
  const [reason, setReason] = useState("");
  const [adding, setAdding] = useState(false);

  const [fraudCases, setFraudCases] = useState<FraudCase[]>([]);
  const [resolvingCaseId, setResolvingCaseId] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;
    return Promise.all([listBlockedDestinations(token), listFraudCases(token, "open")])
      .then(([destinationsData, casesData]) => {
        setDestinations(destinationsData);
        setFraudCases(casesData);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load risk data.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAddDestination(e: FormEvent) {
    e.preventDefault();
    if (!token || !prefix.trim() || !reason.trim()) return;
    setAdding(true);
    setError(null);
    try {
      await addBlockedDestination(token, { prefix: prefix.trim(), reason: reason.trim() });
      setPrefix("");
      setReason("");
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "Only a Super Admin can manage blocked destinations."
          : "Couldn't add the blocked destination."
      );
    } finally {
      setAdding(false);
    }
  }

  async function handleRemoveDestination(ruleId: string) {
    if (!token) return;
    setError(null);
    try {
      await removeBlockedDestination(token, ruleId);
      await load();
    } catch {
      setError("Couldn't remove the blocked destination.");
    }
  }

  async function handleResolveCase(caseId: string, status: "confirmed" | "cleared") {
    if (!token) return;
    const notes = window.prompt(`Notes for marking this case ${status}:`);
    if (!notes) return;
    setResolvingCaseId(caseId);
    setError(null);
    try {
      await resolveFraudCase(token, caseId, status, notes);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "Only a Super Admin or Compliance Officer can resolve a fraud case."
          : "Couldn't resolve the fraud case."
      );
    } finally {
      setResolvingCaseId(null);
    }
  }

  if (!token) return null;

  return (
    <>
      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">Fraud review queue ({fraudCases.length} open)</h3>
        <p className="text-xs text-slate-400">
          Opened automatically when an account&apos;s rolling risk score crosses the review threshold, before (or
          instead of) an automatic suspension.
        </p>

        {fraudCases.length === 0 ? (
          <p className="text-sm text-slate-400">No open fraud cases.</p>
        ) : (
          <ul className="space-y-1.5">
            {fraudCases.map((c) => (
              <li
                key={c.id}
                className="flex items-center justify-between gap-3 text-sm bg-amber-950/30 border border-amber-900/50 rounded-lg px-3 py-2"
              >
                <div>
                  <div className="text-slate-200 font-mono text-xs">{c.account_id}</div>
                  <div className="text-xs text-slate-500">
                    score {c.score_at_open} at open · {new Date(c.created_at).toLocaleString()}
                  </div>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleResolveCase(c.id, "cleared")}
                    disabled={resolvingCaseId === c.id}
                    className="text-xs font-medium rounded-lg px-2.5 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-white"
                  >
                    Clear
                  </button>
                  <button
                    type="button"
                    onClick={() => handleResolveCase(c.id, "confirmed")}
                    disabled={resolvingCaseId === c.id}
                    className="text-xs font-medium rounded-lg px-2.5 py-1 bg-red-900 hover:bg-red-800 disabled:opacity-50 text-white"
                  >
                    Confirm abuse
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">Blocked destinations</h3>
        <p className="text-xs text-slate-400">Super Admin only. Every outbound call is checked against this list.</p>

        <form onSubmit={handleAddDestination} className="flex flex-wrap items-center gap-2">
          <input
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
            placeholder="+1900"
            className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 w-32 placeholder:text-slate-500"
          />
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="reason"
            className="flex-1 min-w-[10rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
          />
          <button
            type="submit"
            disabled={adding || !prefix.trim() || !reason.trim()}
            className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
          >
            {adding ? "Adding..." : "Add"}
          </button>
        </form>

        {destinations.length === 0 ? (
          <p className="text-sm text-slate-400">No blocked destinations configured.</p>
        ) : (
          <ul className="space-y-1.5">
            {destinations.map((d) => (
              <li
                key={d.id}
                className="flex items-center justify-between gap-3 text-sm bg-slate-950 border border-slate-800 rounded-lg px-3 py-2"
              >
                <div>
                  <span className="font-mono text-slate-200">{d.prefix}</span>
                  <span className="text-slate-500 ml-2">{d.reason}</span>
                </div>
                <button
                  type="button"
                  onClick={() => handleRemoveDestination(d.id)}
                  className="text-xs font-medium rounded-lg px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-white shrink-0"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
