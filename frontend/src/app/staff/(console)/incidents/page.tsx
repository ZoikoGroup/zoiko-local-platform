"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  listErrors,
  getErrorSummary,
  getErrorDetail,
  listIncidents,
  createIncident,
  updateIncident,
  resolveIncident,
  runSyntheticChecks,
  listSyntheticChecks,
  getSyntheticCheckSummary,
  ApiError,
  type ErrorEvent,
  type ErrorEventDetail,
  type ErrorCountSummary,
  type Incident,
  type IncidentStatus,
  type SyntheticCheckRun,
  type SyntheticCheckSummary,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const SECTIONS = ["errors", "incidents", "synthetic"] as const;
type Section = (typeof SECTIONS)[number];

export default function StaffIncidentsPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [section, setSection] = useState<Section>("errors");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  if (!token) return null;

  return (
    <>
      <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1 w-fit">
        {SECTIONS.map((s) => (
          <button
            key={s}
            onClick={() => setSection(s)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium capitalize transition ${
              section === s ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {s === "errors" ? "Errors" : s === "incidents" ? "Incidents" : "Synthetic Checks"}
          </button>
        ))}
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      {section === "errors" && <ErrorsSection token={token} onError={setError} />}
      {section === "incidents" && <IncidentsSection token={token} onError={setError} />}
      {section === "synthetic" && <SyntheticChecksSection token={token} onError={setError} />}
    </>
  );
}

function useAuthErrorHandler(onError: (message: string | null) => void) {
  const router = useRouter();
  return useCallback(
    (err: unknown, fallback: string) => {
      if (err instanceof ApiError && err.status === 401) {
        clearStaffToken();
        router.replace("/staff/login");
        return;
      }
      onError(fallback);
    },
    [router, onError]
  );
}

function ErrorsSection({
  token,
  onError,
}: {
  token: string;
  onError: (message: string | null) => void;
}) {
  const handleAuthError = useAuthErrorHandler(onError);
  const [hours, setHours] = useState(24);
  const [summary, setSummary] = useState<ErrorCountSummary[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [events, setEvents] = useState<ErrorEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ErrorEventDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadSummary = useCallback(() => {
    return Promise.resolve()
      .then(() => {
        setSummaryLoading(true);
        return getErrorSummary(token, hours);
      })
      .then((data) => {
        setSummary(data);
        onError(null);
      })
      .catch((err) => handleAuthError(err, "Couldn't load error summary."))
      .finally(() => setSummaryLoading(false));
  }, [token, hours, handleAuthError, onError]);

  const loadEvents = useCallback(() => {
    return Promise.resolve()
      .then(() => {
        setEventsLoading(true);
        return listErrors(token, 100);
      })
      .then((data) => {
        setEvents(data);
        onError(null);
      })
      .catch((err) => handleAuthError(err, "Couldn't load recent errors."))
      .finally(() => setEventsLoading(false));
  }, [token, handleAuthError, onError]);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  function handleExpand(id: string) {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(id);
    setDetail(null);
    setDetailLoading(true);
    getErrorDetail(token, id)
      .then(setDetail)
      .catch(() => onError("Couldn't load error detail."))
      .finally(() => setDetailLoading(false));
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">Window:</span>
          {[1, 24, 168].map((h) => (
            <button
              key={h}
              onClick={() => setHours(h)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition ${
                hours === h ? "bg-slate-700 text-white" : "bg-slate-900 text-slate-400 hover:text-slate-200"
              }`}
            >
              {h === 1 ? "1h" : h === 24 ? "24h" : "7d"}
            </button>
          ))}
        </div>
        <button
          onClick={() => {
            loadSummary();
            loadEvents();
          }}
          className="text-xs text-slate-400 hover:text-white transition"
        >
          Refresh
        </button>
      </div>

      <div className="pt-2 space-y-3">
        <h3 className="text-sm font-semibold text-white">By exception & path</h3>
        {summaryLoading && <p className="text-sm text-slate-400">Loading...</p>}
        {!summaryLoading && summary.length === 0 && (
          <p className="text-sm text-slate-400">No errors in this window.</p>
        )}
        {summary.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400 border-b border-slate-800">
                  <th className="pb-2 pr-4 font-medium">Exception</th>
                  <th className="pb-2 pr-4 font-medium">Path</th>
                  <th className="pb-2 pr-4 font-medium">Status</th>
                  <th className="pb-2 font-medium">Count</th>
                </tr>
              </thead>
              <tbody>
                {summary.map((row, i) => (
                  <tr key={i} className="border-b border-slate-900">
                    <td className="py-2 pr-4 text-slate-200 font-mono text-xs">{row.exception_type ?? "—"}</td>
                    <td className="py-2 pr-4 text-slate-400 font-mono text-xs">{row.path}</td>
                    <td className="py-2 pr-4 text-slate-300">{row.status_code}</td>
                    <td className={`py-2 font-medium ${row.count > 5 ? "text-red-400" : "text-slate-300"}`}>
                      {row.count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="pt-6 border-t border-slate-800 space-y-3">
        <h3 className="text-sm font-semibold text-white">Recent errors</h3>
        {eventsLoading && <p className="text-sm text-slate-400">Loading...</p>}
        {!eventsLoading && events.length === 0 && (
          <p className="text-sm text-slate-400">No recent errors recorded.</p>
        )}
        <div className="space-y-1.5">
          {events.map((e) => (
            <div key={e.id} className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
              <button
                onClick={() => handleExpand(e.id)}
                className="w-full flex items-center justify-between gap-3 text-sm px-3 py-2 text-left hover:bg-slate-800/50 transition"
              >
                <span className="text-slate-200">
                  <span className="font-mono text-xs text-slate-400 mr-2">{e.method}</span>
                  {e.path}
                  {e.exception_type && <span className="text-slate-500 ml-2 text-xs">{e.exception_type}</span>}
                </span>
                <span className="flex items-center gap-3 shrink-0 text-xs">
                  <span className="text-red-400 font-medium">{e.status_code}</span>
                  <span className="text-slate-500">{new Date(e.created_at).toLocaleString()}</span>
                </span>
              </button>
              {expandedId === e.id && (
                <div className="px-3 pb-3 border-t border-slate-800 pt-2">
                  {detailLoading && <p className="text-xs text-slate-400">Loading detail...</p>}
                  {detail && (
                    <div className="space-y-1.5 text-xs">
                      <div className="text-slate-400">
                        Request ID: <span className="font-mono text-slate-300">{detail.request_id}</span>
                      </div>
                      {detail.exception_message && <div className="text-slate-300">{detail.exception_message}</div>}
                      {detail.traceback && (
                        <pre className="bg-slate-950 border border-slate-800 rounded-lg p-3 text-slate-400 overflow-x-auto whitespace-pre-wrap">
                          {detail.traceback}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

const SELECTABLE_STATUSES: IncidentStatus[] = ["investigating", "monitoring"];

function statusBadgeClass(status: IncidentStatus): string {
  if (status === "investigating") return "bg-red-950 text-red-400";
  if (status === "monitoring") return "bg-amber-950 text-amber-400";
  return "bg-emerald-950 text-emerald-400";
}

function IncidentsSection({
  token,
  onError,
}: {
  token: string;
  onError: (message: string | null) => void;
}) {
  const handleAuthError = useAuthErrorHandler(onError);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ title: "", affected_service: "", impact_summary: "" });
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [mitigationDraft, setMitigationDraft] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    return Promise.resolve()
      .then(() => {
        setLoading(true);
        return listIncidents(50);
      })
      .then((data) => {
        setIncidents(data);
        onError(null);
      })
      .catch((err) => handleAuthError(err, "Couldn't load incidents."))
      .finally(() => setLoading(false));
  }, [handleAuthError, onError]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!form.title.trim() || !form.affected_service.trim() || !form.impact_summary.trim()) return;
    setCreating(true);
    onError(null);
    try {
      await createIncident(token, {
        title: form.title.trim(),
        affected_service: form.affected_service.trim(),
        impact_summary: form.impact_summary.trim(),
      });
      setForm({ title: "", affected_service: "", impact_summary: "" });
      setShowForm(false);
      await load();
    } catch (err) {
      onError(
        err instanceof ApiError && err.status === 403
          ? "You don't have permission to declare an incident."
          : "Couldn't create the incident."
      );
    } finally {
      setCreating(false);
    }
  }

  async function handleStatusChange(incident: Incident, status: IncidentStatus) {
    setBusyId(incident.id);
    try {
      await updateIncident(token, incident.id, {
        status,
        mitigation_summary: mitigationDraft[incident.id] ?? incident.mitigation_summary ?? undefined,
      });
      await load();
    } catch {
      onError("Couldn't update this incident.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleResolve(incidentId: string) {
    setBusyId(incidentId);
    try {
      await resolveIncident(token, incidentId);
      await load();
    } catch {
      onError("Couldn't resolve this incident.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-400">Feeds the public status page and subscriber emails.</p>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white"
        >
          {showForm ? "Cancel" : "Declare incident"}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
          <input
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            placeholder="Title"
            className="w-full text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-3 py-1.5 placeholder:text-slate-500"
          />
          <input
            value={form.affected_service}
            onChange={(e) => setForm((f) => ({ ...f, affected_service: e.target.value }))}
            placeholder="Affected service (e.g. voice, video, numbers)"
            className="w-full text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-3 py-1.5 placeholder:text-slate-500"
          />
          <textarea
            value={form.impact_summary}
            onChange={(e) => setForm((f) => ({ ...f, impact_summary: e.target.value }))}
            placeholder="Impact summary"
            rows={2}
            className="w-full text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-3 py-1.5 placeholder:text-slate-500"
          />
          <button
            type="submit"
            disabled={creating}
            className="text-xs font-medium rounded-lg px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-60 text-white"
          >
            {creating ? "Declaring..." : "Declare"}
          </button>
        </form>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}
      {!loading && incidents.length === 0 && <p className="text-sm text-slate-400">No incidents recorded.</p>}

      <div className="space-y-3">
        {incidents.map((inc) => (
          <div key={inc.id} className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-medium text-white">{inc.title}</div>
                <div className="text-xs text-slate-400 mt-0.5">
                  {inc.affected_service} · started {new Date(inc.started_at).toLocaleString()}
                </div>
              </div>
              <span className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize ${statusBadgeClass(inc.status)}`}>
                {inc.status}
              </span>
            </div>

            <p className="text-sm text-slate-300 mt-3">{inc.impact_summary}</p>
            {inc.mitigation_summary && (
              <p className="text-sm text-slate-400 mt-2">
                <span className="text-slate-500">Mitigation: </span>
                {inc.mitigation_summary}
              </p>
            )}

            {inc.status !== "resolved" && (
              <div className="mt-4 pt-4 border-t border-slate-800 flex flex-wrap items-center gap-2">
                <select
                  value={inc.status}
                  onChange={(e) => handleStatusChange(inc, e.target.value as IncidentStatus)}
                  disabled={busyId === inc.id}
                  className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2 py-1.5"
                >
                  {SELECTABLE_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                <input
                  value={mitigationDraft[inc.id] ?? inc.mitigation_summary ?? ""}
                  onChange={(e) => setMitigationDraft((d) => ({ ...d, [inc.id]: e.target.value }))}
                  placeholder="Mitigation update (optional)"
                  className="flex-1 min-w-40 text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
                />
                <button
                  onClick={() => handleResolve(inc.id)}
                  disabled={busyId === inc.id}
                  className="text-sm bg-emerald-700 hover:bg-emerald-600 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 transition"
                >
                  Resolve
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

function SyntheticChecksSection({
  token,
  onError,
}: {
  token: string;
  onError: (message: string | null) => void;
}) {
  const handleAuthError = useAuthErrorHandler(onError);
  const [summary, setSummary] = useState<SyntheticCheckSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<SyntheticCheckRun[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  const loadSummary = useCallback(() => {
    return Promise.resolve()
      .then(() => {
        setLoading(true);
        return getSyntheticCheckSummary(token);
      })
      .then((data) => {
        setSummary(data);
        onError(null);
      })
      .catch((err) => handleAuthError(err, "Couldn't load synthetic check summary."))
      .finally(() => setLoading(false));
  }, [token, handleAuthError, onError]);

  const loadHistory = useCallback(() => {
    return Promise.resolve()
      .then(() => {
        setHistoryLoading(true);
        return listSyntheticChecks(token, { limit: 100 });
      })
      .then(setHistory)
      .catch(() => {})
      .finally(() => setHistoryLoading(false));
  }, [token]);

  useEffect(() => {
    loadSummary();
    loadHistory();
  }, [loadSummary, loadHistory]);

  async function handleRun() {
    setRunning(true);
    onError(null);
    try {
      await runSyntheticChecks(token);
      await Promise.all([loadSummary(), loadHistory()]);
    } catch {
      onError("Couldn't run synthetic checks.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-400">
          On-demand health probes against real provider paths — no automatic scheduler runs these yet.
        </p>
        <button
          onClick={handleRun}
          disabled={running}
          className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
        >
          {running ? "Running..." : "Run now"}
        </button>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {summary && (
        <div
          className={`rounded-lg px-4 py-3 text-sm font-medium ${
            summary.overall_healthy
              ? "bg-emerald-950/60 text-emerald-400 border border-emerald-900"
              : "bg-red-950/60 text-red-400 border border-red-900"
          }`}
        >
          {summary.overall_healthy ? "All checks passing" : "One or more checks failing"}
        </div>
      )}

      <div className="pt-2 space-y-1.5">
        {historyLoading && <p className="text-sm text-slate-400">Loading history...</p>}
        {!historyLoading && history.length === 0 && (
          <p className="text-sm text-slate-400">No synthetic checks have run yet.</p>
        )}
        {history.map((run) => (
          <div
            key={run.id}
            className="flex items-center justify-between gap-3 text-sm bg-slate-900 border border-slate-800 rounded-lg px-3 py-2"
          >
            <div>
              <span className="text-slate-200">{run.check_name}</span>
              {run.detail && <span className="text-slate-500 ml-2 text-xs">{run.detail}</span>}
            </div>
            <span className="flex items-center gap-3 shrink-0 text-xs">
              <span className="text-slate-400">{Math.round(run.duration_ms)}ms</span>
              <span className={run.success ? "text-emerald-400" : "text-red-400"}>
                {run.success ? "pass" : "fail"}
              </span>
              <span className="text-slate-500">{new Date(run.created_at).toLocaleString()}</span>
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
