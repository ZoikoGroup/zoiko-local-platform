"use client";

import { useCallback, useEffect, useState } from "react";
import StatCard from "@/components/StatCard";
import {
  getAnalyticsOverview,
  exportAnalyticsCsv,
  getCurrentUser,
  listCalls,
  listVoicemails,
  listVideoRooms,
  listReceptionistCalls,
  listUsage,
  ApiError,
  type AnalyticsOverview,
  type CallLogEntry,
  type VoicemailEntry,
  type VideoRoom,
  type ReceptionistCallEntry,
  type UsageEvent,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

// Same pragmatic stand-in as the dashboard home page - there's no dedicated
// count endpoint yet, so a generous page size is fetched and counted
// client-side rather than fabricating a number.
const COUNT_LIMIT = 500;

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function Bar({ label, count, total, colorClass }: { label: string; count: number; total: number; colorClass: string }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-1">
        <span className="text-slate-700">{label}</span>
        <span className="text-slate-500">
          {count} ({pct}%)
        </span>
      </div>
      <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full ${colorClass}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

const RANGE_OPTIONS = [7, 30, 90] as const;

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="text-2xl font-semibold text-slate-900 mt-1.5 tracking-tight">{value}</div>
    </div>
  );
}

function TrendChart({ overview }: { overview: AnalyticsOverview }) {
  const width = 640;
  const height = 180;
  const padding = 8;
  const points = overview.daily;

  if (points.length === 0) {
    return (
      <div className="h-44 flex items-center justify-center text-sm text-slate-400 border border-dashed border-slate-200 rounded-lg">
        No activity in this range yet.
      </div>
    );
  }

  const maxCalls = Math.max(1, ...points.map((p) => p.calls));
  const maxMinutes = Math.max(1, ...points.map((p) => p.call_minutes + p.video_minutes));

  const xFor = (i: number) =>
    points.length === 1 ? width / 2 : padding + (i / (points.length - 1)) * (width - padding * 2);
  const yFor = (value: number, max: number) => height - padding - (value / max) * (height - padding * 2);

  const callsPath = points.map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i)},${yFor(p.calls, maxCalls)}`).join(" ");
  const minutesPath = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${xFor(i)},${yFor(p.call_minutes + p.video_minutes, maxMinutes)}`)
    .join(" ");

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-44">
        <path d={callsPath} fill="none" stroke="#4f46e5" strokeWidth={2} />
        <path d={minutesPath} fill="none" stroke="#10b981" strokeWidth={2} />
      </svg>
      <div className="flex items-center gap-4 text-xs text-slate-500 mt-2">
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-indigo-600" /> Calls
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" /> Call + video minutes
        </span>
      </div>
    </div>
  );
}

export default function ReportsPage() {
  const [token] = useState<string | null>(() => getToken());
  const [days, setDays] = useState<(typeof RANGE_OPTIONS)[number]>(30);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const load = useCallback(() => {
    if (!token) return;
    setLoading(true);
    setError(null);
    getAnalyticsOverview(token, days)
      .then(setOverview)
      .catch((err) => {
        const message =
          err instanceof ApiError && err.status === 403
            ? "Only account owners and admins can view reports."
            : "Couldn't load report data.";
        setError(message);
      })
      .finally(() => setLoading(false));
  }, [token, days]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleExport() {
    if (!token) return;
    setExporting(true);
    try {
      const blob = await exportAnalyticsCsv(token, days);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `zoiko-analytics-${days}d.csv`;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("Couldn't export the report.");
    } finally {
      setExporting(false);
    }
  }

  const [detailLoading, setDetailLoading] = useState(true);
  const [detailLoadError, setDetailLoadError] = useState<string | null>(null);

  const [calls, setCalls] = useState<CallLogEntry[]>([]);
  const [voicemails, setVoicemails] = useState<VoicemailEntry[]>([]);
  const [videoRooms, setVideoRooms] = useState<VideoRoom[]>([]);
  const [receptionistCalls, setReceptionistCalls] = useState<ReceptionistCallEntry[]>([]);
  const [usage, setUsage] = useState<UsageEvent[] | null>(null); // null = not permitted / not loaded

  const loadAll = useCallback(() => {
    if (!token) return;
    return Promise.all([
      listCalls(token, COUNT_LIMIT),
      listVoicemails(token),
      listVideoRooms(token),
      listReceptionistCalls(token),
    ])
      .then(([callsData, voicemailsData, videoData, receptionistData]) => {
        setCalls(callsData);
        setVoicemails(voicemailsData);
        setVideoRooms(videoData);
        setReceptionistCalls(receptionistData);
        setDetailLoadError(null);
      })
      .catch(() => setDetailLoadError("Couldn't load some of your report data."))
      .finally(() => setDetailLoading(false));
  }, [token]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (!token) return;
    // Usage events are Owner/Admin only - a 403 here just means this
    // section stays hidden, not a page-wide error.
    getCurrentUser(token)
      .then((me) => {
        if (me.role === "owner" || me.role === "admin") {
          return listUsage(token).then(setUsage);
        }
        return undefined;
      })
      .catch(() => {
        // not permitted or failed - section stays hidden
      });
  }, [token]);

  const inboundCalls = calls.filter((c) => c.direction === "inbound");
  const outboundCalls = calls.filter((c) => c.direction === "outbound");
  const spamCalls = calls.filter((c) => c.is_suspected_spam);
  const callDurations = calls.filter((c) => c.duration !== null).map((c) => c.duration as number);
  const avgCallDuration = average(callDurations);

  const voicemailDurations = voicemails.filter((v) => v.duration !== null).map((v) => v.duration as number);
  const avgVoicemailDuration = average(voicemailDurations);

  const endedVideoCalls = videoRooms.filter((r) => r.status === "ended" || r.status === "active");
  const totalParticipantMinutes = videoRooms.reduce((sum, r) => sum + r.participant_minutes, 0);
  const confidentialVideoCalls = videoRooms.filter((r) => r.confidential);

  const escalatedReceptionistCalls = receptionistCalls.filter((c) => c.escalated);
  const spamReceptionistCalls = receptionistCalls.filter((c) => c.is_likely_spam);
  const urgencyCounts = {
    high: receptionistCalls.filter((c) => c.urgency === "high").length,
    medium: receptionistCalls.filter((c) => c.urgency === "medium").length,
    low: receptionistCalls.filter((c) => c.urgency === "low").length,
  };

  const usageByType = new Map<string, { quantity: number; unit: string }>();
  for (const event of usage ?? []) {
    const existing = usageByType.get(event.event_type);
    if (existing) {
      existing.quantity += event.quantity;
    } else {
      usageByType.set(event.event_type, { quantity: event.quantity, unit: event.unit });
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Reports</h2>
          <p className="text-sm text-slate-500">
            Usage trends across calls, video, and messaging, plus call quality and AI performance.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-slate-300 overflow-hidden">
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option}
                onClick={() => setDays(option)}
                className={`text-xs font-medium px-3 py-1.5 ${
                  days === option ? "bg-indigo-600 text-white" : "bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                {option}d
              </button>
            ))}
          </div>
          <button
            onClick={handleExport}
            disabled={!overview || exporting}
            className="text-xs font-medium rounded-lg px-3 py-1.5 bg-slate-800 hover:bg-slate-900 disabled:opacity-60 text-white"
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
        </div>
      </div>

      {loading && <p className="text-sm text-slate-500">Loading...</p>}
      {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

      {overview && !loading && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            <Metric label="Total Calls" value={String(overview.total_calls)} />
            <Metric label="Call Minutes" value={overview.total_call_minutes.toLocaleString()} />
            <Metric label="Video Minutes" value={overview.total_video_minutes.toLocaleString()} />
            <Metric label="Messages" value={String(overview.total_messages)} />
            <Metric label="Active Numbers" value={String(overview.active_numbers)} />
            <Metric label="AI Summaries" value={String(overview.ai_summaries)} />
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-slate-900 mb-4">Usage Trend ({overview.range_days} days)</h3>
            <TrendChart overview={overview} />
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-slate-900 mb-4">Daily Breakdown</h3>
            {overview.daily.length === 0 ? (
              <p className="text-sm text-slate-500">No activity in this range yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-slate-400 border-b border-slate-100">
                      <th className="py-2 pr-4 font-medium">Date</th>
                      <th className="py-2 pr-4 font-medium">Calls</th>
                      <th className="py-2 pr-4 font-medium">Call Minutes</th>
                      <th className="py-2 pr-4 font-medium">Video Minutes</th>
                      <th className="py-2 pr-4 font-medium">Messages</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...overview.daily].reverse().map((point) => (
                      <tr key={point.date} className="border-b border-slate-50 last:border-0">
                        <td className="py-2 pr-4 text-slate-700">{point.date}</td>
                        <td className="py-2 pr-4 text-slate-600">{point.calls}</td>
                        <td className="py-2 pr-4 text-slate-600">{point.call_minutes}</td>
                        <td className="py-2 pr-4 text-slate-600">{point.video_minutes}</td>
                        <td className="py-2 pr-4 text-slate-600">{point.messages}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {detailLoadError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{detailLoadError}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Calls" value={detailLoading ? "…" : calls.length.toLocaleString()} />
        <StatCard label="Avg Call Duration" value={detailLoading ? "…" : formatDuration(avgCallDuration || null)} />
        <StatCard
          label="Suspected Spam Rate"
          value={
            detailLoading || calls.length === 0 ? "…" : `${((spamCalls.length / calls.length) * 100).toFixed(1)}%`
          }
        />
        <StatCard
          label="Video Participant-Minutes"
          value={detailLoading ? "…" : Math.round(totalParticipantMinutes).toLocaleString()}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
          <h3 className="font-semibold text-slate-900">Call Traffic</h3>
          {detailLoading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : calls.length === 0 ? (
            <p className="text-sm text-slate-500">No calls yet.</p>
          ) : (
            <div className="space-y-3">
              <Bar label="Inbound" count={inboundCalls.length} total={calls.length} colorClass="bg-indigo-500" />
              <Bar label="Outbound" count={outboundCalls.length} total={calls.length} colorClass="bg-emerald-500" />
              <Bar label="Suspected spam (inbound)" count={spamCalls.length} total={calls.length} colorClass="bg-red-500" />
            </div>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
          <h3 className="font-semibold text-slate-900">Voicemail</h3>
          {detailLoading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : voicemails.length === 0 ? (
            <p className="text-sm text-slate-500">No voicemails yet.</p>
          ) : (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="text-2xl font-semibold text-slate-900">{voicemails.length}</div>
                <div className="text-xs text-slate-500">Total voicemails</div>
              </div>
              <div>
                <div className="text-2xl font-semibold text-slate-900">{formatDuration(avgVoicemailDuration || null)}</div>
                <div className="text-xs text-slate-500">Average length</div>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
          <h3 className="font-semibold text-slate-900">Video Calls</h3>
          {detailLoading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : videoRooms.length === 0 ? (
            <p className="text-sm text-slate-500">No video calls yet.</p>
          ) : (
            <div className="grid grid-cols-3 gap-4">
              <div>
                <div className="text-2xl font-semibold text-slate-900">{endedVideoCalls.length}</div>
                <div className="text-xs text-slate-500">Total calls</div>
              </div>
              <div>
                <div className="text-2xl font-semibold text-slate-900">{Math.round(totalParticipantMinutes)}</div>
                <div className="text-xs text-slate-500">Participant-minutes</div>
              </div>
              <div>
                <div className="text-2xl font-semibold text-slate-900">{confidentialVideoCalls.length}</div>
                <div className="text-xs text-slate-500">Confidential calls</div>
              </div>
            </div>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
          <h3 className="font-semibold text-slate-900">AI Receptionist</h3>
          {detailLoading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : receptionistCalls.length === 0 ? (
            <p className="text-sm text-slate-500">No AI receptionist calls yet.</p>
          ) : (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <div className="text-2xl font-semibold text-slate-900">{receptionistCalls.length}</div>
                  <div className="text-xs text-slate-500">Calls handled</div>
                </div>
                <div>
                  <div className="text-2xl font-semibold text-slate-900">{escalatedReceptionistCalls.length}</div>
                  <div className="text-xs text-slate-500">Escalated</div>
                </div>
                <div>
                  <div className="text-2xl font-semibold text-slate-900">{spamReceptionistCalls.length}</div>
                  <div className="text-xs text-slate-500">Flagged as spam</div>
                </div>
              </div>
              <div className="space-y-2 pt-1">
                <Bar label="High urgency" count={urgencyCounts.high} total={receptionistCalls.length} colorClass="bg-red-500" />
                <Bar label="Medium urgency" count={urgencyCounts.medium} total={receptionistCalls.length} colorClass="bg-amber-500" />
                <Bar label="Low urgency" count={urgencyCounts.low} total={receptionistCalls.length} colorClass="bg-slate-400" />
              </div>
            </div>
          )}
        </div>
      </div>

      {usage !== null && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
          <h3 className="font-semibold text-slate-900">Usage &amp; Billing Events</h3>
          {usage.length === 0 ? (
            <p className="text-sm text-slate-500">No billable usage recorded yet.</p>
          ) : (
            <ul className="space-y-2">
              {[...usageByType.entries()].map(([eventType, { quantity, unit }]) => (
                <li key={eventType} className="flex items-center justify-between text-sm">
                  <span className="text-slate-700">{eventType.replace(/_/g, " ")}</span>
                  <span className="text-slate-500">
                    {quantity.toLocaleString()} {unit}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
