"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { listStaffAuditEvents, ApiError, type AuditEvent } from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

export default function StaffAuditPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
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
        <p className="text-xs text-slate-500">Most recent 200 events, across every account.</p>
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

      {loading && <p className="text-sm text-slate-500">Loading...</p>}

      {!loading && events.length === 0 && (
        <p className="text-sm text-slate-500">No audit events yet.</p>
      )}

      <div className="space-y-2">
        {events.map((e) => (
          <div
            key={e.id}
            className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-3 flex items-start justify-between gap-4"
          >
            <div>
              <div className="text-sm text-white font-medium">{e.action}</div>
              <div className="text-xs text-slate-500 mt-0.5">
                {e.target} · actor <span className="font-mono">{e.actor}</span>
              </div>
              {e.reason && <div className="text-xs text-slate-400 mt-1">{e.reason}</div>}
            </div>
            <div className="text-xs text-slate-500 whitespace-nowrap">
              {new Date(e.created_at).toLocaleString()}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
