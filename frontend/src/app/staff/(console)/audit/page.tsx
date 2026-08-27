"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { listStaffAuditEvents, ApiError, type AuditEvent } from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";
import { useStaffRole } from "@/lib/staffRole";

export default function StaffAuditPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const { role } = useStaffRole();
  const isSuperAdmin = role === "super_admin";
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const loadEvents = useCallback(() => {
    if (!token) return;
    return listStaffAuditEvents(token)
      .then((data) => {
        setEvents(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load audit events.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  if (!token) return null;

  return (
    <>
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-400">Most recent 200 events, across every account.</p>
        <button
          onClick={loadEvents}
          className="text-xs text-slate-400 hover:text-white transition"
        >
          Refresh
        </button>
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {!loading && events.length === 0 && (
        <p className="text-sm text-slate-400">No audit events yet.</p>
      )}

      <div className="space-y-2">
        {events.map((e) => (
          <div
            key={e.id}
            className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 flex items-start justify-between gap-4"
          >
            <div className="min-w-0">
              <div className="text-sm text-white font-medium">{e.action}</div>
              <div className="text-xs text-slate-400 mt-0.5">
                {e.target} · actor <span className="font-mono">{e.actor}</span>
              </div>
              {e.reason && <div className="text-xs text-slate-400 mt-1">{e.reason}</div>}
              {/* Forensic/integrity fields (tamper-evidence hash chain,
                  cross-request correlation) - real signal for a Super
                  Admin investigating exactly what changed and tracing it
                  across services, but not something a SUPPORT/COMPLIANCE_
                  OFFICER lookup needs cluttering a routine "who did this"
                  check. */}
              {isSuperAdmin && (e.correlation_id || e.before_hash || e.after_hash) && (
                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10.5px] text-slate-600">
                  {e.correlation_id && <span title="Correlation ID">corr:{e.correlation_id}</span>}
                  {e.before_hash && <span title="Before-state hash">before:{e.before_hash.slice(0, 12)}…</span>}
                  {e.after_hash && <span title="After-state hash">after:{e.after_hash.slice(0, 12)}…</span>}
                </div>
              )}
            </div>
            <div className="text-xs text-slate-400 whitespace-nowrap">
              {new Date(e.created_at).toLocaleString()}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
