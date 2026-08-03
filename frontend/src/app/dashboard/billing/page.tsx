"use client";

import { useEffect, useState } from "react";
import { listUsage, ApiError, type UsageEvent } from "@/lib/api";
import { getToken } from "@/lib/auth";

function formatQuantity(event: UsageEvent): string {
  if (event.unit === "seconds") {
    const minutes = Math.floor(event.quantity / 60);
    const seconds = Math.round(event.quantity % 60);
    return `${minutes}m ${seconds}s`;
  }
  return `${event.quantity} ${event.unit}`;
}

export default function BillingPage() {
  const [token] = useState<string | null>(() => getToken());
  const [events, setEvents] = useState<UsageEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listUsage(token)
      .then((data) => {
        setEvents(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 403) {
          setError("Usage is visible to account Owners and Admins only.");
        } else {
          setError("Couldn't load usage.");
        }
      })
      .finally(() => setLoading(false));
  }, [token]);

  const totalCallSeconds = events
    .filter((e) => e.event_type === "call_seconds")
    .reduce((sum, e) => sum + e.quantity, 0);

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Billing &amp; Usage</h2>
        <p className="text-sm text-slate-500">
          Metered usage on your account. Plans, invoices, and payment methods are coming in a later stage.
        </p>
      </div>

      {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Call minutes this account</h3>
        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {!loading && !error && (
          <div className="text-2xl font-semibold text-slate-900">
            {Math.floor(totalCallSeconds / 60)}
            <span className="text-sm font-normal text-slate-500 ml-1">minutes</span>
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Usage log</h3>

        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {!loading && !error && events.length === 0 && (
          <p className="text-sm text-slate-500">No metered usage yet.</p>
        )}

        <div className="space-y-2">
          {events.map((e) => (
            <div
              key={e.id}
              className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3 text-sm"
            >
              <div>
                <span className="font-medium text-slate-800 capitalize">
                  {e.event_type.replaceAll("_", " ")}
                </span>
                {e.country_band && <span className="ml-2 text-xs text-slate-400">{e.country_band}</span>}
              </div>
              <div className="flex items-center gap-4">
                <span className="text-slate-600">{formatQuantity(e)}</span>
                <span className="text-xs text-slate-400">{new Date(e.created_at).toLocaleString()}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
