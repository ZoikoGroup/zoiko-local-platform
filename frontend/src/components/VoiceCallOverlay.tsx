"use client";

import { useVoiceDevice } from "@/lib/voiceDevice";

/**
 * Rendered once in dashboard/layout.tsx so it's visible from any dashboard
 * page - an inbound call rings the browser (see voiceDevice.tsx) no matter
 * where the person is working, not just while the Calls page is open.
 */
export default function VoiceCallOverlay() {
  const { status, error, incomingFrom, activeCallTo, acceptIncoming, rejectIncoming, hangUp } =
    useVoiceDevice();

  if (status === "incoming") {
    return (
      <div className="fixed top-4 right-4 z-50 w-80 rounded-xl border border-indigo-200 bg-white shadow-lg p-4 space-y-3">
        <div>
          <p className="text-xs font-medium text-indigo-600 uppercase tracking-wide">Incoming call</p>
          <p className="text-lg font-mono font-semibold text-slate-900">{incomingFrom || "Unknown caller"}</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={acceptIncoming}
            className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            Accept
          </button>
          <button
            type="button"
            onClick={rejectIncoming}
            className="flex-1 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            Reject
          </button>
        </div>
      </div>
    );
  }

  if (status === "in-call" && activeCallTo) {
    return (
      <div className="fixed top-4 right-4 z-50 w-80 rounded-xl border border-emerald-200 bg-white shadow-lg p-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-emerald-600 uppercase tracking-wide">● Live</p>
          <p className="text-sm font-mono text-slate-900">{activeCallTo}</p>
        </div>
        <button
          type="button"
          onClick={hangUp}
          className="bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg px-4 py-2"
        >
          Hang Up
        </button>
      </div>
    );
  }

  if (status === "error" && error) {
    return (
      <div className="fixed top-4 right-4 z-50 w-80 rounded-xl border border-red-200 bg-white shadow-lg p-4">
        <p className="text-sm text-red-600">{error}</p>
      </div>
    );
  }

  return null;
}
