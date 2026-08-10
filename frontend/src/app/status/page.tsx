"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getPublicStatus, ApiError, type PublicStatus } from "@/lib/api";

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

export default function StatusPage() {
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    function load() {
      getPublicStatus()
        .then((data) => {
          if (!cancelled) {
            setStatus(data);
            setError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) {
            setError(err instanceof ApiError ? err.message : "Couldn't load status.");
          }
        });
    }
    load();
    const interval = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <main className="min-h-screen bg-slate-50 flex flex-col items-center px-6 py-16">
      <div className="w-full max-w-xl space-y-6">
        <div className="flex items-center gap-2 justify-center">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 text-white flex items-center justify-center font-bold text-sm">
            Z
          </div>
          <span className="font-semibold text-slate-900">Zoiko Local</span>
        </div>

        <div className="bg-white rounded-2xl shadow-xl shadow-slate-200/60 border border-slate-100 p-8 space-y-6">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">System Status</h1>
            <p className="text-sm text-slate-500 mt-1">
              Live status of the services behind numbers, calling, video, and AI Receptionist.
            </p>
          </div>

          {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

          {!status && !error && <p className="text-sm text-slate-500">Loading...</p>}

          {status && (
            <>
              <div
                className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium ${
                  status.overall === "operational"
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-amber-50 text-amber-700"
                }`}
              >
                <Dot status={status.overall} />
                {status.overall === "operational"
                  ? "All systems operational"
                  : "Some systems are experiencing degraded performance"}
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

          <p className="text-xs text-slate-600 text-center">Updates automatically every 30 seconds.</p>
        </div>

        <p className="text-xs text-slate-400 text-center">
          <Link href="/login" className="text-indigo-600 hover:text-indigo-700">
            Back to sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
