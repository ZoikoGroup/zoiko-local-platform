"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getAnalyticsOverview,
  exportAnalyticsCsv,
  ApiError,
  type AnalyticsOverview,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Reports</h2>
          <p className="text-sm text-slate-500">Usage trends across calls, video, and messaging.</p>
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
    </div>
  );
}
