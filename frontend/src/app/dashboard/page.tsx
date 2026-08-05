"use client";

import { useEffect, useState, useCallback } from "react";
import StatCard from "@/components/StatCard";
import {
  listMyNumbers,
  listCalls,
  listVoicemails,
  listVideoRooms,
  listSummaries,
  listReceptionistCalls,
  type MyPhoneNumber,
  type CallLogEntry,
  type VoicemailEntry,
  type VideoRoom,
  type SummaryListEntry,
  type ReceptionistCallEntry,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

// Fetches a generous page of each list so the dashboard's counts are a real
// total for any account at this stage of usage, not a literal "last 20"
// (the backend's own default page size for /media/voice/calls) — there's no
// dedicated count endpoint yet, so this is the pragmatic real-data stand-in
// rather than a fabricated number.
const COUNT_LIMIT = 500;

type ActivityItem = {
  key: string;
  label: string;
  detail: string;
  status: string;
  ts: number;
};

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function DashboardPage() {
  const [token] = useState<string | null>(() => getToken());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [numbers, setNumbers] = useState<MyPhoneNumber[]>([]);
  const [calls, setCalls] = useState<CallLogEntry[]>([]);
  const [voicemails, setVoicemails] = useState<VoicemailEntry[]>([]);
  const [videoRooms, setVideoRooms] = useState<VideoRoom[]>([]);
  const [summaries, setSummaries] = useState<SummaryListEntry[]>([]);
  const [receptionistCalls, setReceptionistCalls] = useState<ReceptionistCallEntry[]>([]);
  // Date.now() is impure to call directly during render (react-hooks/purity) -
  // captured once per data load in an effect instead, the sanctioned escape
  // hatch for this kind of intentional impurity.
  const [nowTs, setNowTs] = useState<number | null>(null);

  const loadAll = useCallback(() => {
    if (!token) return;
    return Promise.all([
      listMyNumbers(token),
      listCalls(token, COUNT_LIMIT),
      listVoicemails(token),
      listVideoRooms(token),
      listSummaries(token),
      listReceptionistCalls(token),
    ])
      .then(([numbersData, callsData, voicemailsData, videoData, summariesData, receptionistData]) => {
        setNumbers(numbersData);
        setCalls(callsData);
        setVoicemails(voicemailsData);
        setVideoRooms(videoData);
        setSummaries(summariesData);
        setReceptionistCalls(receptionistData);
        setNowTs(Date.now());
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load some of your dashboard data."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const activeNumbers = numbers.filter((n) => n.status === "active").length;
  const callMinutes = calls.reduce((sum, c) => sum + (c.duration ?? 0), 0) / 60;
  const videoMinutes = videoRooms.reduce((sum, r) => sum + r.participant_minutes, 0);
  const aiInteractions = summaries.length + receptionistCalls.length;

  const weekMs = 7 * 24 * 60 * 60 * 1000;
  const now = nowTs ?? 0;
  const callsThisWeek = nowTs === null ? 0 : calls.filter((c) => now - new Date(c.created_at).getTime() <= weekMs).length;
  const videoMinutesThisWeek =
    nowTs === null
      ? 0
      : videoRooms
          .filter((r) => r.started_at && now - new Date(r.started_at).getTime() <= weekMs)
          .reduce((sum, r) => sum + r.participant_minutes, 0);

  const activity: ActivityItem[] = [
    ...calls.map((c) => ({
      key: `call:${c.id}`,
      label: c.direction === "inbound" ? "Incoming Call" : "Outbound Call",
      detail: c.direction === "inbound" ? c.from : c.to,
      status: c.duration !== null ? formatDuration(c.duration) : c.status,
      ts: new Date(c.created_at).getTime(),
    })),
    ...voicemails.map((v) => ({
      key: `voicemail:${v.id}`,
      label: "Voicemail Received",
      detail: v.from,
      status: formatDuration(v.duration),
      ts: new Date(v.created_at).getTime(),
    })),
    ...videoRooms
      .filter((r) => r.started_at)
      .map((r) => ({
        key: `video:${r.room_name}`,
        label: "Video Call",
        detail: r.room_name,
        status: r.status.charAt(0).toUpperCase() + r.status.slice(1),
        ts: new Date(r.started_at as string).getTime(),
      })),
    ...summaries.map((s) => ({
      key: `summary:${s.id}`,
      label: "AI Summary Generated",
      detail: s.summary.length > 60 ? `${s.summary.slice(0, 60)}…` : s.summary,
      status: "Completed",
      ts: new Date(s.created_at).getTime(),
    })),
  ]
    .sort((a, b) => b.ts - a.ts)
    .slice(0, 6);

  const countryCounts = new Map<string, number>();
  for (const n of numbers) {
    countryCounts.set(n.country, (countryCounts.get(n.country) ?? 0) + 1);
  }
  const topCountries = [...countryCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([country, count]) => ({
      country,
      value: numbers.length > 0 ? `${count} (${((count / numbers.length) * 100).toFixed(1)}%)` : `${count}`,
    }));

  const aiInsights = [
    {
      label: "Call Summary",
      detail: `${summaries.filter((s) => s.source_type === "call").length} calls summarized by AI`,
    },
    {
      label: "Voicemail Intelligence",
      detail: `${summaries.filter((s) => s.source_type === "voicemail").length} voicemails transcribed & summarized`,
    },
    {
      label: "AI Receptionist",
      detail: `${receptionistCalls.length} calls captured automatically`,
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Welcome back 👋</h2>
        <p className="text-sm text-slate-500">
          Here&apos;s what&apos;s happening with your communication platform.
        </p>
      </div>

      {loadError && (
        <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{loadError}</p>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <StatCard label="Total Calls" value={loading ? "…" : calls.length.toLocaleString()} />
        <StatCard label="Video Minutes" value={loading ? "…" : Math.round(videoMinutes).toLocaleString()} />
        <StatCard label="Total Minutes" value={loading ? "…" : Math.round(callMinutes + videoMinutes).toLocaleString()} />
        <StatCard label="Active Numbers" value={loading ? "…" : activeNumbers.toLocaleString()} />
        <StatCard label="AI Interactions" value={loading ? "…" : aiInteractions.toLocaleString()} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-slate-900 mb-4">This Week</h3>
          <div className="h-56 flex flex-col items-center justify-center gap-2 text-slate-500 border border-dashed border-slate-200 rounded-lg">
            {loading ? (
              <span className="text-sm">Loading…</span>
            ) : (
              <>
                <div className="text-3xl font-semibold text-slate-900">{callsThisWeek}</div>
                <div className="text-sm">calls in the last 7 days</div>
                <div className="text-lg font-semibold text-slate-900 mt-3">
                  {Math.round(videoMinutesThisWeek)}
                </div>
                <div className="text-sm">video minutes in the last 7 days</div>
                <p className="text-xs text-slate-600 mt-2">Trend charts are coming in a later stage.</p>
              </>
            )}
          </div>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-slate-900">Recent Activity</h3>
          </div>
          {loading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : activity.length === 0 ? (
            <p className="text-sm text-slate-500">No activity yet.</p>
          ) : (
            <ul className="space-y-3">
              {activity.map((item) => (
                <li key={item.key} className="flex items-start justify-between text-sm">
                  <div className="min-w-0">
                    <div className="font-medium text-slate-800">{item.label}</div>
                    <div className="text-slate-500 text-xs truncate">{item.detail}</div>
                  </div>
                  <div className="text-right shrink-0 pl-3">
                    <div className="text-xs text-slate-600">{timeAgo(new Date(item.ts).toISOString())}</div>
                    <div className="text-xs text-emerald-600">{item.status}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-slate-900 mb-4">Top Countries (Numbers)</h3>
          {loading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : topCountries.length === 0 ? (
            <p className="text-sm text-slate-500">No numbers yet.</p>
          ) : (
            <ul className="space-y-3">
              {topCountries.map((row) => (
                <li key={row.country} className="flex items-center justify-between text-sm">
                  <span className="text-slate-700">{row.country}</span>
                  <span className="text-slate-500">{row.value}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-slate-900 mb-4">AI Insights</h3>
          <div className="grid sm:grid-cols-3 gap-4">
            {aiInsights.map((insight) => (
              <div key={insight.label} className="rounded-lg border border-slate-100 p-4">
                <div className="text-sm font-medium text-slate-800">{insight.label}</div>
                <div className="text-xs text-slate-500 mt-1">{loading ? "Loading…" : insight.detail}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
