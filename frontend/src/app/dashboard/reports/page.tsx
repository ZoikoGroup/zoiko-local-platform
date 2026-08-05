"use client";

import { useEffect, useState, useCallback } from "react";
import StatCard from "@/components/StatCard";
import {
  getCurrentUser,
  listCalls,
  listVoicemails,
  listVideoRooms,
  listReceptionistCalls,
  listUsage,
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

export default function ReportsPage() {
  const [token] = useState<string | null>(() => getToken());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

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
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load some of your report data."))
      .finally(() => setLoading(false));
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
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Reports</h2>
        <p className="text-sm text-slate-500">Usage, call quality, and AI performance across your account.</p>
      </div>

      {loadError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{loadError}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Calls" value={loading ? "…" : calls.length.toLocaleString()} />
        <StatCard label="Avg Call Duration" value={loading ? "…" : formatDuration(avgCallDuration || null)} />
        <StatCard
          label="Suspected Spam Rate"
          value={loading || calls.length === 0 ? "…" : `${((spamCalls.length / calls.length) * 100).toFixed(1)}%`}
        />
        <StatCard label="Video Participant-Minutes" value={loading ? "…" : Math.round(totalParticipantMinutes).toLocaleString()} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
          <h3 className="font-semibold text-slate-900">Call Traffic</h3>
          {loading ? (
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
          {loading ? (
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
          {loading ? (
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
          {loading ? (
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
