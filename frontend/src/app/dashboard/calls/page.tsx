"use client";

import { useEffect, useState, useCallback } from "react";
import {
  listMyNumbers,
  listCalls,
  placeOutboundCall,
  summarizeCall,
  grantAiProcessingConsent,
  ApiError,
  type MyPhoneNumber,
  type CallLogEntry,
} from "@/lib/api";
import { getToken } from "@/lib/auth";
import { CallRow, type SummaryKey, type SummaryState } from "@/components/CallRow";

export default function CallsPage() {
  const [token] = useState<string | null>(() => getToken());

  const [numbers, setNumbers] = useState<MyPhoneNumber[]>([]);
  const [calls, setCalls] = useState<CallLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [toNumber, setToNumber] = useState("");
  const [fromNumber, setFromNumber] = useState("");
  const [callBusy, setCallBusy] = useState(false);
  const [callError, setCallError] = useState<string | null>(null);
  const [callSuccess, setCallSuccess] = useState<string | null>(null);

  const [summaries, setSummaries] = useState<Record<SummaryKey, SummaryState>>({});

  const loadAll = useCallback(() => {
    if (!token) return;
    return Promise.all([listMyNumbers(token), listCalls(token)])
      .then(([numbersData, callsData]) => {
        setNumbers(numbersData);
        setCalls(callsData);
        setLoadError(null);
        setFromNumber((current) => current || numbersData.find((n) => n.status === "active")?.e164 || "");
      })
      .catch(() => setLoadError("Couldn't load calls."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function handlePlaceCall(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setCallBusy(true);
    setCallError(null);
    setCallSuccess(null);
    try {
      const result = await placeOutboundCall(token, { to: toNumber, from: fromNumber });
      setCallSuccess(`Call placed to ${result.to} — status: ${result.status}`);
      setToNumber("");
      await loadAll();
    } catch (err) {
      setCallError(err instanceof ApiError ? err.message : "Couldn't place the call.");
    } finally {
      setCallBusy(false);
    }
  }

  async function handleSummarize(id: string) {
    if (!token) return;
    const key: SummaryKey = `call:${id}`;
    setSummaries((prev) => ({ ...prev, [key]: { status: "busy" } }));
    try {
      const result = await summarizeCall(token, id);
      setSummaries((prev) => ({ ...prev, [key]: { status: "done", result } }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403 && err.message.toLowerCase().includes("consent")) {
        setSummaries((prev) => ({ ...prev, [key]: { status: "consent_required" } }));
        return;
      }
      const message = err instanceof ApiError ? err.message : "Couldn't generate a summary.";
      setSummaries((prev) => ({ ...prev, [key]: { status: "error", message } }));
    }
  }

  async function handleGrantConsent(id: string) {
    if (!token) return;
    try {
      await grantAiProcessingConsent(token);
      await handleSummarize(id);
    } catch {
      const key: SummaryKey = `call:${id}`;
      setSummaries((prev) => ({ ...prev, [key]: { status: "error", message: "Couldn't grant AI consent." } }));
    }
  }

  const activeNumbers = numbers.filter((n) => n.status === "active");

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Calls</h2>
        <p className="text-sm text-slate-500">Inbound and outbound call logs, with AI-generated summaries.</p>
      </div>

      {loadError && (
        <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{loadError}</p>
      )}

      {/* Make a call */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Make a call</h3>

        {activeNumbers.length === 0 && !loading ? (
          <p className="text-sm text-slate-500">
            You don&apos;t have an active number to call from yet — get one from Phone Numbers first.
          </p>
        ) : (
          <form onSubmit={handlePlaceCall} className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">From</label>
              <select
                value={fromNumber}
                onChange={(e) => setFromNumber(e.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono"
              >
                {activeNumbers.map((n) => (
                  <option key={n.id} value={n.e164}>
                    {n.e164}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">To</label>
              <input
                type="tel"
                required
                value={toNumber}
                onChange={(e) => setToNumber(e.target.value)}
                placeholder="+15551234567"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono placeholder:text-slate-400"
              />
            </div>
            <button
              type="submit"
              disabled={callBusy || !fromNumber}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
            >
              {callBusy ? "Calling..." : "Call"}
            </button>
          </form>
        )}

        {callError && <p className="text-sm text-red-600">{callError}</p>}
        {callSuccess && <p className="text-sm text-emerald-600 bg-emerald-50 rounded-lg px-3 py-2">{callSuccess}</p>}
      </div>

      {/* Call log */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Call log</h3>

        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {!loading && calls.length === 0 && <p className="text-sm text-slate-500">No calls yet.</p>}

        <div className="space-y-3">
          {calls.map((c) => (
            <CallRow
              key={c.id}
              label={`${c.direction === "inbound" ? c.from : c.to} · ${c.direction}`}
              status={c.status}
              duration={c.duration}
              createdAt={c.created_at}
              recordingUrl={c.recording_url}
              suspectedSpam={c.is_suspected_spam}
              summaryState={summaries[`call:${c.id}`] ?? { status: "idle" }}
              onSummarize={() => handleSummarize(c.id)}
              onGrantConsent={() => handleGrantConsent(c.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
