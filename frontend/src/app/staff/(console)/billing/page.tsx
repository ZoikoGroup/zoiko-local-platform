"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  listStaffAccounts,
  simulateZoikoNexPaymentEvent,
  listZoikoNexSyncLog,
  getZoikoNexReconciliation,
  ApiError,
  type AccountOverview,
  type ZoikoNexSyncEvent,
  type ZoikoNexReconciliationSummary,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const EVENT_TYPES = [
  { value: "payment_failed", label: "Payment failed" },
  { value: "payment_retry", label: "Payment retry" },
  { value: "payment_restored", label: "Payment restored" },
] as const;

export default function StaffBillingPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();

  const [accounts, setAccounts] = useState<AccountOverview[]>([]);
  const [reconciliation, setReconciliation] = useState<ZoikoNexReconciliationSummary | null>(null);
  const [syncLog, setSyncLog] = useState<ZoikoNexSyncEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [eventType, setEventType] = useState<(typeof EVENT_TYPES)[number]["value"]>("payment_failed");
  const [simulating, setSimulating] = useState(false);
  const [simulateResult, setSimulateResult] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;
    return Promise.all([listStaffAccounts(token), getZoikoNexReconciliation(token), listZoikoNexSyncLog(token, { limit: 50 })])
      .then(([accountsData, reconciliationData, syncLogData]) => {
        setAccounts(accountsData);
        setReconciliation(reconciliationData);
        setSyncLog(syncLogData);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load billing data.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSimulate(e: FormEvent) {
    e.preventDefault();
    if (!token || !selectedAccountId) return;
    setSimulating(true);
    setSimulateResult(null);
    setError(null);
    try {
      const sub = await simulateZoikoNexPaymentEvent(token, selectedAccountId, eventType);
      setSimulateResult(`Subscription status is now "${sub.status}".`);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.status === 403
            ? "Only a Super Admin can simulate a payment event."
            : err.message
          : "Couldn't simulate the payment event."
      );
    } finally {
      setSimulating(false);
    }
  }

  if (!token) return null;

  return (
    <>
      <div>
        <p className="text-xs text-slate-400">
          There is no real ZoikoNex integration yet - this console simulates the payment-state webhook a real
          ZoikoNex would eventually send, so graceful degradation can be exercised end-to-end.
        </p>
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {reconciliation && (
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-semibold text-white">Reconciliation</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
            <div>
              <div className="text-xl font-semibold text-white">
                {reconciliation.synced_subscriptions}/{reconciliation.total_subscriptions}
              </div>
              <div className="text-xs text-slate-400">Subscriptions synced</div>
            </div>
            <div>
              <div className={`text-xl font-semibold ${reconciliation.unsynced_subscriptions > 0 ? "text-amber-400" : "text-white"}`}>
                {reconciliation.unsynced_subscriptions}
              </div>
              <div className="text-xs text-slate-400">Subscriptions unsynced</div>
            </div>
            <div>
              <div className="text-xl font-semibold text-white">
                {reconciliation.synced_usage_events}/{reconciliation.total_usage_events}
              </div>
              <div className="text-xs text-slate-400">Usage events synced</div>
            </div>
            <div>
              <div className={`text-xl font-semibold ${reconciliation.unsynced_usage_events > 0 ? "text-amber-400" : "text-white"}`}>
                {reconciliation.unsynced_usage_events}
              </div>
              <div className="text-xs text-slate-400">Usage events unsynced</div>
            </div>
          </div>
        </div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">Simulate a payment event</h3>
        <p className="text-xs text-slate-400">Super Admin only. Applies graceful degradation immediately.</p>

        <form onSubmit={handleSimulate} className="flex flex-wrap items-center gap-2">
          <select
            value={selectedAccountId}
            onChange={(e) => setSelectedAccountId(e.target.value)}
            required
            className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5"
          >
            <option value="">Select an account...</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} ({a.owner_email ?? "no owner"})
              </option>
            ))}
          </select>
          <select
            value={eventType}
            onChange={(e) => setEventType(e.target.value as typeof eventType)}
            className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5"
          >
            {EVENT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={simulating || !selectedAccountId}
            className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
          >
            {simulating ? "Simulating..." : "Simulate"}
          </button>
        </form>

        {simulateResult && <p className="text-xs text-emerald-400">{simulateResult}</p>}
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">Recent sync events</h3>
        {syncLog.length === 0 ? (
          <p className="text-sm text-slate-400">No sync events yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {syncLog.map((event) => (
              <li
                key={event.id}
                className="flex items-center justify-between gap-3 text-sm bg-slate-950 border border-slate-800 rounded-lg px-3 py-2"
              >
                <span className="text-slate-200 capitalize">{event.event_type.replaceAll("_", " ")}</span>
                <span className="flex items-center gap-3 shrink-0 text-xs text-slate-400">
                  <span className="font-mono">{event.zoikonex_ref ?? "—"}</span>
                  <span>{new Date(event.created_at).toLocaleString()}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
