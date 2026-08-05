"use client";

import { useEffect, useState, useCallback } from "react";
import { getPublicStatus, listMyNumbers, ApiError, type PublicStatus, type MyPhoneNumber } from "@/lib/api";
import { getToken } from "@/lib/auth";

function Dot({ status }: { status: "operational" | "degraded" }) {
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full ${
        status === "operational" ? "bg-emerald-500" : "bg-amber-500"
      }`}
    />
  );
}

function statusLabel(status: "operational" | "degraded") {
  return status === "operational" ? "Operational" : "Degraded performance";
}

export default function IntegrationsPage() {
  const [token] = useState<string | null>(() => getToken());
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [numbers, setNumbers] = useState<MyPhoneNumber[]>([]);
  const [loading, setLoading] = useState(true);

  const loadStatus = useCallback(() => {
    return getPublicStatus()
      .then((data) => {
        setStatus(data);
        setStatusError(null);
      })
      .catch((err) => setStatusError(err instanceof ApiError ? err.message : "Couldn't load provider status."));
  }, []);

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 30_000);
    return () => clearInterval(interval);
  }, [loadStatus]);

  useEffect(() => {
    if (!token) return;
    listMyNumbers(token)
      .then(setNumbers)
      .finally(() => setLoading(false));
  }, [token]);

  const activeNumbers = numbers.filter((n) => n.status === "active");
  const forwardingCount = activeNumbers.filter((n) => n.forwarding_number).length;
  const receptionistCount = activeNumbers.filter((n) => n.ai_receptionist_enabled).length;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Integrations</h2>
        <p className="text-sm text-slate-500">
          Zoiko Local runs your calling, video, and AI on our managed provider connections — there&apos;s nothing
          for you to configure or connect yourself.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-900">Platform Services</h3>
          <span className="text-xs text-slate-500">Updates every 30s</span>
        </div>

        {statusError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{statusError}</p>}
        {!status && !statusError && <p className="text-sm text-slate-500">Loading...</p>}

        {status && (
          <>
            <div
              className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium ${
                status.overall === "operational" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
              }`}
            >
              <Dot status={status.overall} />
              {status.overall === "operational"
                ? "All connected services operational"
                : "Some connected services are experiencing degraded performance"}
            </div>

            <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {status.components.map((c) => (
                <div key={c.name} className="flex items-center justify-between px-4 py-3 text-sm">
                  <span className="text-slate-700">{c.name}</span>
                  <span className="flex items-center gap-2 text-slate-500">
                    <Dot status={c.status} />
                    {statusLabel(c.status)}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <h3 className="font-semibold text-slate-900">Your Connected Numbers</h3>
        {loading ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : activeNumbers.length === 0 ? (
          <p className="text-sm text-slate-500">No active numbers yet — add one from the Numbers page.</p>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <div>
              <div className="text-2xl font-semibold text-slate-900">{activeNumbers.length}</div>
              <div className="text-xs text-slate-500">Active numbers</div>
            </div>
            <div>
              <div className="text-2xl font-semibold text-slate-900">{forwardingCount}</div>
              <div className="text-xs text-slate-500">With call forwarding</div>
            </div>
            <div>
              <div className="text-2xl font-semibold text-slate-900">{receptionistCount}</div>
              <div className="text-xs text-slate-500">With AI Receptionist</div>
            </div>
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-2">
        <h3 className="font-semibold text-slate-900">Billing</h3>
        <p className="text-sm text-slate-500">
          Billing and payment integration isn&apos;t connected yet — this account isn&apos;t being charged.
        </p>
      </div>
    </div>
  );
}
