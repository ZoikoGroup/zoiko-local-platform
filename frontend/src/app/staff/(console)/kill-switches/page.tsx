"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  listKillSwitches,
  activateKillSwitch,
  deactivateKillSwitch,
  ApiError,
  KILL_SWITCH_SCOPES,
  type KillSwitch,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const SCOPE_LABELS: Record<string, string> = {
  number_provisioning: "New Number Provisioning",
  number_release: "Number Release / Cancellation",
  outbound_calling: "New Outbound Calling",
  ai_processing: "AI Processing (transcription, summaries, receptionist)",
  payments_billing: "Payments & Billing",
};

const SCOPE_DESCRIPTIONS: Record<string, string> = {
  number_provisioning: "Blocks new number purchases platform-wide. Existing numbers keep working.",
  number_release: "Blocks customers cancelling numbers. Use if a bug is wrongly releasing numbers.",
  outbound_calling: "Blocks new outbound calls from being placed. Calls already in progress are unaffected.",
  ai_processing: "Blocks new transcription, AI summaries, and AI Receptionist responses.",
  payments_billing: "Blocks new payment/billing actions. Does not affect service already delivered.",
};

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export default function StaffKillSwitchesPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [switches, setSwitches] = useState<KillSwitch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [activatingScope, setActivatingScope] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [busyScope, setBusyScope] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;
    return listKillSwitches(token)
      .then((data) => {
        setSwitches(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load kill switch status.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  function switchFor(scope: string): KillSwitch | undefined {
    return switches.find((s) => s.scope === scope);
  }

  async function handleActivate(e: FormEvent, scope: string) {
    e.preventDefault();
    if (!token) return;
    setBusyScope(scope);
    setError(null);
    try {
      await activateKillSwitch(token, scope, reason.trim() || null, expiresAt ? new Date(expiresAt).toISOString() : null);
      setActivatingScope(null);
      setReason("");
      setExpiresAt("");
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "Only staff with the ops.manage_kill_switches capability can do this."
          : "Couldn't activate that kill switch."
      );
    } finally {
      setBusyScope(null);
    }
  }

  async function handleDeactivate(scope: string) {
    if (!token) return;
    setBusyScope(scope);
    setError(null);
    try {
      await deactivateKillSwitch(token, scope);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "Only staff with the ops.manage_kill_switches capability can do this."
          : "Couldn't deactivate that kill switch."
      );
    } finally {
      setBusyScope(null);
    }
  }

  if (!token) return null;

  return (
    <>
      <p className="text-xs text-slate-400">
        Platform-wide emergency controls (Commercial Billing Operating Standard doc §32.1) - each switch stops{" "}
        <em>new</em> activity in one scope without touching anything already in progress or destroying customer
        evidence. Activating or deactivating requires the ops.manage_kill_switches capability (Super Admin, by
        default).
      </p>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {!loading && (
        <div className="space-y-3">
          {KILL_SWITCH_SCOPES.map((scope) => {
            const sw = switchFor(scope);
            const isActive = sw?.is_active ?? false;
            return (
              <div
                key={scope}
                className={`rounded-lg border p-4 ${
                  isActive ? "border-red-900 bg-red-950/30" : "border-slate-800 bg-slate-900"
                }`}
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span
                        className={`h-2 w-2 rounded-full ${isActive ? "bg-red-500" : "bg-emerald-500"}`}
                        aria-hidden
                      />
                      <span className="font-medium text-sm text-slate-100">{SCOPE_LABELS[scope]}</span>
                      <span
                        className={`text-[10px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 ${
                          isActive ? "bg-red-900 text-red-200" : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {isActive ? "Active - blocking" : "Inactive"}
                      </span>
                    </div>
                    <p className="text-xs text-slate-500 mt-1">{SCOPE_DESCRIPTIONS[scope]}</p>
                    {isActive && (
                      <div className="text-xs text-slate-400 mt-2 space-y-0.5">
                        {sw?.reason && <div>Reason: {sw.reason}</div>}
                        <div>Activated: {formatDate(sw?.activated_at ?? null)}</div>
                        {sw?.expires_at && <div>Expires: {formatDate(sw.expires_at)}</div>}
                      </div>
                    )}
                  </div>

                  {isActive ? (
                    <button
                      type="button"
                      onClick={() => handleDeactivate(scope)}
                      disabled={busyScope === scope}
                      className="shrink-0 text-xs font-medium rounded-lg px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white"
                    >
                      {busyScope === scope ? "Deactivating..." : "Deactivate"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setActivatingScope(activatingScope === scope ? null : scope)}
                      className="shrink-0 text-xs font-medium rounded-lg px-3 py-1.5 bg-red-700 hover:bg-red-600 text-white"
                    >
                      Activate
                    </button>
                  )}
                </div>

                {activatingScope === scope && !isActive && (
                  <form
                    onSubmit={(e) => handleActivate(e, scope)}
                    className="mt-3 pt-3 border-t border-slate-800 flex flex-wrap items-center gap-2"
                  >
                    <input
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="Reason (e.g. investigating a billing bug)"
                      className="flex-1 min-w-[16rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
                    />
                    <input
                      type="datetime-local"
                      value={expiresAt}
                      onChange={(e) => setExpiresAt(e.target.value)}
                      title="Optional auto-expiry"
                      className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5"
                    />
                    <button
                      type="submit"
                      disabled={busyScope === scope}
                      className="text-xs font-medium rounded-lg px-3 py-1.5 bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white"
                    >
                      {busyScope === scope ? "Activating..." : "Confirm activate"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setActivatingScope(null)}
                      className="text-xs font-medium rounded-lg px-3 py-1.5 border border-slate-700 text-slate-300 hover:bg-slate-800"
                    >
                      Cancel
                    </button>
                  </form>
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
