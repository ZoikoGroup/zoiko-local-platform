"use client";

import { useEffect, useState, useCallback } from "react";
import {
  listSummaries,
  listReceptionistCalls,
  type SummaryListEntry,
  type ReceptionistCallEntry,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const URGENCY_STYLES: Record<string, { badge: string; border: string }> = {
  high: { badge: "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200", border: "border-l-red-400" },
  medium: { badge: "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200", border: "border-l-amber-400" },
  low: { badge: "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200", border: "border-l-slate-300" },
};

const SOURCE_STYLES: Record<string, string> = {
  voicemail: "bg-indigo-50 text-indigo-600",
  call: "bg-emerald-50 text-emerald-600",
};

function SparkleIcon({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 3l1.6 4.8L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.2L12 3ZM19 15l.8 2.4L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.6L19 15Z"
      />
    </svg>
  );
}

function PhoneSmallIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M4 5c0-1 1-2 2-2h2l2 5-2 1.5a10 10 0 0 0 5 5L14.5 13l5 2v2c0 1-1 2-2 2C10 19 4 13 4 5Z"
      />
    </svg>
  );
}

function ClockSmallIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="9" />
      <path strokeLinecap="round" d="M12 7v5l3.5 2" />
    </svg>
  );
}

function ChevronIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 6l6 6-6 6" />
    </svg>
  );
}

function VoicemailIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="6" cy="15" r="3" />
      <circle cx="18" cy="15" r="3" />
      <path strokeLinecap="round" d="M6 12H18" />
    </svg>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <div className="w-10 h-10 rounded-full bg-slate-50 flex items-center justify-center text-slate-300">
        <SparkleIcon className="w-5 h-5" />
      </div>
      <p className="text-sm text-slate-400">{label}</p>
    </div>
  );
}

function initials(name: string | null): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return parts
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

export default function AIInsightsPage() {
  const [token] = useState<string | null>(() => getToken());

  const [summaries, setSummaries] = useState<SummaryListEntry[]>([]);
  const [summariesLoading, setSummariesLoading] = useState(true);
  const [summariesError, setSummariesError] = useState<string | null>(null);

  const [receptionistCalls, setReceptionistCalls] = useState<ReceptionistCallEntry[]>([]);
  const [receptionistLoading, setReceptionistLoading] = useState(true);
  const [receptionistError, setReceptionistError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!token) return;
    listSummaries(token)
      .then((data) => {
        setSummaries(data);
        setSummariesError(null);
      })
      .catch(() => setSummariesError("Couldn't load AI summaries."))
      .finally(() => setSummariesLoading(false));
    listReceptionistCalls(token)
      .then((data) => {
        setReceptionistCalls(data);
        setReceptionistError(null);
      })
      .catch(() => setReceptionistError("Couldn't load AI Receptionist calls."))
      .finally(() => setReceptionistLoading(false));
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-indigo-600 text-white flex items-center justify-center shrink-0">
          <SparkleIcon className="w-5 h-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold text-slate-900">AI Insights</h2>
          <p className="text-sm text-slate-500">Voicemail transcription, call summaries, and the AI Receptionist.</p>
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-6 py-5 border-b border-slate-100">
          <h3 className="font-semibold text-slate-900">Call &amp; Voicemail Summaries</h3>
          <p className="text-xs text-slate-400 mt-0.5">
            AI-generated — may be inaccurate, not an authoritative record.
          </p>
        </div>

        {summariesLoading && <p className="text-sm text-slate-500 px-6 py-5">Loading...</p>}
        {summariesError && <p className="text-sm text-red-600 px-6 py-5">{summariesError}</p>}
        {!summariesLoading && summaries.length === 0 && (
          <EmptyState label="No AI summaries yet — summarize a voicemail or call from the Calls page to see it here." />
        )}

        <ul className="divide-y divide-slate-100">
          {summaries.map((s) => (
            <li key={s.id} className="px-6 py-4 flex gap-3.5">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${
                  SOURCE_STYLES[s.source_type] ?? "bg-slate-100 text-slate-500"
                }`}
              >
                {s.source_type === "voicemail" ? (
                  <VoicemailIcon className="w-4 h-4" />
                ) : (
                  <PhoneSmallIcon className="w-4 h-4" />
                )}
              </div>
              <div className="flex-1 min-w-0 space-y-1">
                <div className="flex items-center justify-between gap-4">
                  <span className="text-xs font-medium text-slate-500 capitalize">
                    {s.source_type.replaceAll("_", " ")}
                  </span>
                  <span className="text-xs text-slate-400 shrink-0 flex items-center gap-1">
                    <ClockSmallIcon />
                    {new Date(s.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="text-sm text-slate-800 leading-relaxed">{s.summary}</p>
                <p className="text-[11px] text-slate-400 font-mono">{s.model_version}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-6 py-5 border-b border-slate-100">
          <h3 className="font-semibold text-slate-900">AI Receptionist</h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Caller qualification captured when a number has the AI Receptionist enabled.
          </p>
        </div>

        {receptionistLoading && <p className="text-sm text-slate-500 px-6 py-5">Loading...</p>}
        {receptionistError && <p className="text-sm text-red-600 px-6 py-5">{receptionistError}</p>}
        {!receptionistLoading && receptionistCalls.length === 0 && (
          <EmptyState label="No AI Receptionist calls yet." />
        )}

        <ul className="divide-y divide-slate-100">
          {receptionistCalls.map((c) => {
            const urgencyStyle = c.urgency ? URGENCY_STYLES[c.urgency] : null;
            return (
              <li
                key={c.id}
                className={`px-6 py-4 border-l-4 ${urgencyStyle?.border ?? "border-l-transparent"}`}
              >
                <div className="flex gap-3.5">
                  <div className="w-9 h-9 rounded-full bg-indigo-100 text-indigo-700 text-xs font-semibold flex items-center justify-center shrink-0">
                    {initials(c.caller_name)}
                  </div>
                  <div className="flex-1 min-w-0 space-y-1.5">
                    <div className="flex items-center justify-between gap-4 flex-wrap">
                      <div className="text-sm font-semibold text-slate-800">
                        {c.caller_name ?? "Unknown caller"}
                        {c.caller_company && (
                          <span className="text-slate-400 font-normal"> · {c.caller_company}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {c.escalated && (
                          <span className="text-xs font-medium text-white bg-indigo-600 rounded-full px-2.5 py-1">
                            Escalated
                          </span>
                        )}
                        {c.urgency && (
                          <span
                            className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize ${
                              urgencyStyle?.badge ?? "bg-slate-100 text-slate-600"
                            }`}
                          >
                            {c.urgency} urgency
                          </span>
                        )}
                      </div>
                    </div>

                    <p className="text-sm text-slate-700 leading-relaxed">
                      {c.summary ?? c.reason ?? "No summary available for this call."}
                    </p>

                    <div className="flex items-center gap-4 text-xs text-slate-400">
                      <span className="flex items-center gap-1">
                        <PhoneSmallIcon />
                        {c.caller_number}
                      </span>
                      <span className="flex items-center gap-1">
                        <ClockSmallIcon />
                        {new Date(c.created_at).toLocaleString()}
                      </span>
                    </div>

                    <details className="group text-xs">
                      <summary className="cursor-pointer list-none flex items-center gap-1 text-slate-500 hover:text-slate-700 w-fit">
                        <ChevronIcon className="w-3 h-3 transition-transform group-open:rotate-90" />
                        Raw transcript
                      </summary>
                      <p className="mt-2 text-slate-500 bg-slate-50 rounded-lg px-3 py-2 leading-relaxed">
                        {c.raw_transcript}
                      </p>
                    </details>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
