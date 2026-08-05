"use client";

import { useEffect, useState, useCallback } from "react";
import {
  listSummaries,
  searchSummaries,
  editSummary,
  listReceptionistCalls,
  assignReceptionistCall,
  editReceptionistCallSummary,
  listTeamMembers,
  ApiError,
  type SummaryListEntry,
  type ReceptionistCallEntry,
  type TeamMember,
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
      <p className="text-sm text-slate-600">{label}</p>
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

  const [searchInput, setSearchInput] = useState("");
  const [searchActive, setSearchActive] = useState(false);
  const [searching, setSearching] = useState(false);

  const [receptionistCalls, setReceptionistCalls] = useState<ReceptionistCallEntry[]>([]);
  const [receptionistLoading, setReceptionistLoading] = useState(true);
  const [receptionistError, setReceptionistError] = useState<string | null>(null);

  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);
  const [routingCallId, setRoutingCallId] = useState<string | null>(null);
  const [routingSelection, setRoutingSelection] = useState<Record<string, string>>({});
  const [routingError, setRoutingError] = useState<Record<string, string>>({});
  const [routedNotice, setRoutedNotice] = useState<string | null>(null);

  const [editingSummaryId, setEditingSummaryId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [editingCallId, setEditingCallId] = useState<string | null>(null);
  const [editCallText, setEditCallText] = useState("");
  const [editCallBusy, setEditCallBusy] = useState(false);
  const [editCallError, setEditCallError] = useState<string | null>(null);

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
    listTeamMembers(token)
      .then(setTeamMembers)
      .catch(() => {
        // Members without team-management access still see receptionist
        // calls, just without the ability to route them - not worth
        // surfacing as an error.
      });
  }, [token]);

  async function handleRouteCall(callId: string) {
    if (!token) return;
    const assignedUserId = routingSelection[callId];
    if (!assignedUserId) return;
    setRoutingError((prev) => ({ ...prev, [callId]: "" }));
    try {
      await assignReceptionistCall(token, callId, assignedUserId);
      const assignee = teamMembers.find((m) => m.id === assignedUserId);
      setReceptionistCalls((prev) =>
        prev.map((c) =>
          c.id === callId
            ? { ...c, assigned_user_id: assignedUserId, assigned_user_email: assignee?.email ?? null }
            : c
        )
      );
      setRoutingCallId(null);
      setRoutedNotice(`Routed to ${assignee?.email ?? "team member"} successfully`);
      setTimeout(() => setRoutedNotice(null), 4000);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Couldn't route this call.";
      setRoutingError((prev) => ({ ...prev, [callId]: message }));
    }
  }

  async function handleUnassignCall(callId: string) {
    if (!token) return;
    try {
      await assignReceptionistCall(token, callId, null);
      setReceptionistCalls((prev) =>
        prev.map((c) => (c.id === callId ? { ...c, assigned_user_id: null, assigned_user_email: null } : c))
      );
    } catch {
      setRoutingError((prev) => ({ ...prev, [callId]: "Couldn't unassign this call." }));
    }
  }

  useEffect(() => {
    load();
  }, [load]);

  async function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !searchInput.trim()) return;
    setSearching(true);
    setSummariesError(null);
    try {
      setSummaries(await searchSummaries(token, searchInput.trim()));
      setSearchActive(true);
    } catch {
      setSummariesError("Search failed.");
    } finally {
      setSearching(false);
    }
  }

  function handleClearSearch() {
    setSearchInput("");
    setSearchActive(false);
    setSummariesLoading(true);
    load();
  }

  function startEditingSummary(entry: SummaryListEntry) {
    setEditingSummaryId(entry.id);
    setEditText(entry.summary);
    setEditError(null);
  }

  async function handleSaveSummaryEdit(summaryId: string) {
    if (!token || !editText.trim()) return;
    setEditBusy(true);
    setEditError(null);
    try {
      const updated = await editSummary(token, summaryId, editText.trim());
      setSummaries((prev) => prev.map((s) => (s.id === summaryId ? updated : s)));
      setEditingSummaryId(null);
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "Couldn't save this edit.");
    } finally {
      setEditBusy(false);
    }
  }

  function startEditingCall(call: ReceptionistCallEntry) {
    setEditingCallId(call.id);
    setEditCallText(call.summary ?? "");
    setEditCallError(null);
  }

  async function handleSaveCallEdit(callId: string) {
    if (!token || !editCallText.trim()) return;
    setEditCallBusy(true);
    setEditCallError(null);
    try {
      const updated = await editReceptionistCallSummary(token, callId, editCallText.trim());
      setReceptionistCalls((prev) =>
        prev.map((c) =>
          c.id === callId
            ? { ...c, summary: updated.summary, original_summary: updated.original_summary, edited_at: updated.edited_at }
            : c
        )
      );
      setEditingCallId(null);
    } catch (err) {
      setEditCallError(err instanceof ApiError ? err.message : "Couldn't save this edit.");
    } finally {
      setEditCallBusy(false);
    }
  }

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
        <div className="px-6 py-5 border-b border-slate-100 space-y-3">
          <div>
            <h3 className="font-semibold text-slate-900">Call &amp; Voicemail Summaries</h3>
            <p className="text-xs text-slate-600 mt-0.5">
              AI-generated — may be inaccurate, not an authoritative record.
            </p>
          </div>
          <form onSubmit={handleSearchSubmit} className="flex gap-2">
            <input
              type="search"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search summaries and transcripts…"
              className="flex-1 text-sm rounded-lg border border-slate-200 px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
            />
            <button
              type="submit"
              disabled={searching || !searchInput.trim()}
              className="text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg px-3.5 py-1.5 transition"
            >
              {searching ? "Searching…" : "Search"}
            </button>
            {searchActive && (
              <button
                type="button"
                onClick={handleClearSearch}
                className="text-xs font-medium text-slate-500 hover:text-slate-700 px-2"
              >
                Clear
              </button>
            )}
          </form>
        </div>

        {summariesLoading && <p className="text-sm text-slate-500 px-6 py-5">Loading...</p>}
        {summariesError && <p className="text-sm text-red-600 px-6 py-5">{summariesError}</p>}
        {!summariesLoading && summaries.length === 0 && (
          <EmptyState
            label={
              searchActive
                ? "No summaries matched that search."
                : "No AI summaries yet — summarize a voicemail or call from the Calls page to see it here."
            }
          />
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
              <div className="flex-1 min-w-0 space-y-1.5">
                <div className="flex items-center justify-between gap-4">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-slate-500 capitalize">
                      {s.source_type.replaceAll("_", " ")}
                    </span>
                    {s.language && (
                      <span className="text-[10px] font-medium text-slate-400 uppercase border border-slate-200 rounded px-1">
                        {s.language}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {s.urgency && (
                      <span
                        className={`text-xs font-medium rounded-full px-2 py-0.5 capitalize ${
                          URGENCY_STYLES[s.urgency]?.badge ?? "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {s.urgency}
                      </span>
                    )}
                    <span className="text-xs text-slate-400 flex items-center gap-1">
                      <ClockSmallIcon />
                      {new Date(s.created_at).toLocaleString()}
                    </span>
                  </div>
                </div>
                {editingSummaryId === s.id ? (
                  <div className="space-y-1.5">
                    <textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      rows={3}
                      className="w-full text-sm rounded-lg border border-indigo-300 px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                    />
                    {editError && <p className="text-xs text-red-600">{editError}</p>}
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleSaveSummaryEdit(s.id)}
                        disabled={editBusy || !editText.trim()}
                        className="text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg px-3 py-1"
                      >
                        {editBusy ? "Saving…" : "Save correction"}
                      </button>
                      <button
                        onClick={() => setEditingSummaryId(null)}
                        className="text-xs text-slate-400 hover:text-slate-600"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="text-sm text-slate-800 leading-relaxed">{s.summary}</p>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => startEditingSummary(s)}
                        className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                      >
                        Correct this
                      </button>
                      {s.edited_at && (
                        <span className="text-[10px] text-slate-400">
                          Edited {new Date(s.edited_at).toLocaleString()}
                        </span>
                      )}
                    </div>
                  </>
                )}
                {s.suggested_follow_up && (
                  <p className="text-xs text-indigo-700 bg-indigo-50 rounded-lg px-2.5 py-1.5">
                    Suggested: {s.suggested_follow_up}
                  </p>
                )}
                {s.action_items.length > 0 && (
                  <ul className="text-xs text-slate-500 list-disc pl-4 space-y-0.5">
                    {s.action_items.map((item, i) => (
                      <li key={i}>{item}</li>
                    ))}
                  </ul>
                )}
                <p className="text-[11px] text-slate-400 font-mono">{s.model_version}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-6 py-5 border-b border-slate-100">
          <h3 className="font-semibold text-slate-900">AI Receptionist</h3>
          <p className="text-xs text-slate-600 mt-0.5">
            Caller qualification captured when a number has the AI Receptionist enabled.
          </p>
          {routedNotice && (
            <p className="text-xs font-medium text-emerald-700 bg-emerald-50 rounded-lg px-2.5 py-1.5 mt-2 w-fit">
              {routedNotice}
            </p>
          )}
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
                        {c.is_likely_spam && (
                          <span
                            title={c.spam_reason ?? undefined}
                            className="text-xs font-medium text-amber-700 bg-amber-100 rounded-full px-2.5 py-1"
                          >
                            Suspected spam
                          </span>
                        )}
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

                    {editingCallId === c.id ? (
                      <div className="space-y-1.5">
                        <textarea
                          value={editCallText}
                          onChange={(e) => setEditCallText(e.target.value)}
                          rows={3}
                          className="w-full text-sm rounded-lg border border-indigo-300 px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                        />
                        {editCallError && <p className="text-xs text-red-600">{editCallError}</p>}
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleSaveCallEdit(c.id)}
                            disabled={editCallBusy || !editCallText.trim()}
                            className="text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg px-3 py-1"
                          >
                            {editCallBusy ? "Saving…" : "Save correction"}
                          </button>
                          <button
                            onClick={() => setEditingCallId(null)}
                            className="text-xs text-slate-400 hover:text-slate-600"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <p className="text-sm text-slate-700 leading-relaxed">
                          {c.summary ?? c.reason ?? "No summary available for this call."}
                        </p>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => startEditingCall(c)}
                            className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                          >
                            Correct this
                          </button>
                          {c.edited_at && (
                            <span className="text-[10px] text-slate-400">
                              Edited {new Date(c.edited_at).toLocaleString()}
                            </span>
                          )}
                        </div>
                      </>
                    )}

                    {c.guardrail_flags.length > 0 && (
                      <p className="text-xs font-medium text-red-700 bg-red-50 rounded-lg px-2.5 py-1.5">
                        Review before acting — the AI&apos;s summary above may contain a{" "}
                        {c.guardrail_flags.map((f) => f.replaceAll("_", " ")).join(" and ")} that was never
                        actually authorized.
                      </p>
                    )}

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

                    <div className="flex items-center gap-2 flex-wrap">
                      {c.assigned_user_id ? (
                        <>
                          <span className="text-xs font-medium text-emerald-700 bg-emerald-50 rounded-full px-2.5 py-1">
                            Routed to {c.assigned_user_email ?? "team member"}
                          </span>
                          <button
                            onClick={() => handleUnassignCall(c.id)}
                            className="text-xs text-slate-400 hover:text-slate-600"
                          >
                            Unassign
                          </button>
                        </>
                      ) : routingCallId === c.id ? (
                        <>
                          <select
                            value={routingSelection[c.id] ?? ""}
                            onChange={(e) =>
                              setRoutingSelection((prev) => ({ ...prev, [c.id]: e.target.value }))
                            }
                            className="text-xs rounded-lg border border-slate-200 px-2 py-1 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                          >
                            <option value="">Choose team member…</option>
                            {teamMembers.map((m) => (
                              <option key={m.id} value={m.id}>
                                {m.email} ({m.role})
                              </option>
                            ))}
                          </select>
                          <button
                            onClick={() => handleRouteCall(c.id)}
                            disabled={!routingSelection[c.id]}
                            className="text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg px-3 py-1"
                          >
                            Approve &amp; route
                          </button>
                          <button
                            onClick={() => setRoutingCallId(null)}
                            className="text-xs text-slate-400 hover:text-slate-600"
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          onClick={() => setRoutingCallId(c.id)}
                          disabled={teamMembers.length === 0}
                          className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          Route to team member
                        </button>
                      )}
                    </div>
                    {routingError[c.id] && <p className="text-xs text-red-600">{routingError[c.id]}</p>}

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
