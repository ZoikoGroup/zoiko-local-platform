"use client";

import { useState } from "react";
import type { ConversationSummary } from "@/lib/api";

export type SummaryKey = string; // `${kind}:${id}`
export type SummaryState =
  | { status: "idle" }
  | { status: "busy" }
  | { status: "consent_required" }
  | { status: "error"; message: string }
  | { status: "done"; result: ConversationSummary };

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function CallRow({
  label,
  status,
  duration,
  createdAt,
  recordingUrl,
  loadRecording,
  suspectedSpam,
  summaryState,
  onSummarize,
  onGrantConsent,
}: {
  label: string;
  status: string;
  duration: number | null;
  createdAt: string;
  recordingUrl: string | null;
  // Fetches the recording as a Blob through our own backend (see
  // getCallRecordingBlob/getVoicemailRecordingBlob) - the raw Twilio URL
  // in recordingUrl needs Twilio's own credentials to fetch directly, so
  // Play can't just be a plain <a href> to it.
  loadRecording: () => Promise<Blob>;
  suspectedSpam?: boolean;
  summaryState: SummaryState;
  onSummarize: () => void;
  onGrantConsent: () => void;
}) {
  const [playError, setPlayError] = useState<string | null>(null);

  async function handlePlay() {
    setPlayError(null);
    // Open the tab synchronously (within the click gesture) so popup
    // blockers don't kill it while the recording loads asynchronously.
    const win = window.open("", "_blank");
    try {
      const blob = await loadRecording();
      const url = URL.createObjectURL(blob);
      if (win) win.location.href = url;
    } catch {
      win?.close();
      setPlayError("Couldn't load this recording.");
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 px-4 py-3 space-y-2">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-sm font-medium text-slate-800 flex items-center gap-2">
            {label}
            {suspectedSpam && (
              <span
                title="This number called multiple other Zoiko Local accounts in a short window - a pattern typical of spam/robocall traffic, not a confirmed block."
                className="text-[10px] font-semibold uppercase tracking-wide text-amber-700 bg-amber-100 rounded-full px-2 py-0.5"
              >
                Suspected spam
              </span>
            )}
          </div>
          <div className="text-xs text-slate-500">
            {status} · {formatDuration(duration)} · {new Date(createdAt).toLocaleString()}
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {recordingUrl && (
            <button
              type="button"
              onClick={handlePlay}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
            >
              Play
            </button>
          )}
          {recordingUrl && summaryState.status !== "done" && (
            <button
              onClick={onSummarize}
              disabled={summaryState.status === "busy"}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-60"
            >
              {summaryState.status === "busy" ? "Summarizing..." : "Summarize with AI"}
            </button>
          )}
          {!recordingUrl && <span className="text-xs text-slate-400">No recording</span>}
        </div>
      </div>

      {playError && <p className="text-xs text-red-600">{playError}</p>}

      {summaryState.status === "consent_required" && (
        <div className="text-xs bg-amber-50 text-amber-700 rounded-lg px-3 py-2 flex items-center justify-between gap-3">
          <span>AI summaries need your consent to process call/voicemail audio.</span>
          <button onClick={onGrantConsent} className="font-medium underline shrink-0">
            Grant consent &amp; summarize
          </button>
        </div>
      )}

      {summaryState.status === "error" && (
        <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">{summaryState.message}</p>
      )}

      {summaryState.status === "done" && (
        <div className="text-xs bg-slate-50 rounded-lg px-3 py-2 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-slate-700 flex-1">{summaryState.result.summary}</span>
            {summaryState.result.urgency && (
              <span
                className={
                  "shrink-0 rounded-full px-2 py-0.5 font-medium " +
                  (summaryState.result.urgency === "high"
                    ? "bg-red-100 text-red-700"
                    : summaryState.result.urgency === "medium"
                    ? "bg-amber-100 text-amber-700"
                    : "bg-slate-200 text-slate-600")
                }
              >
                {summaryState.result.urgency} urgency
              </span>
            )}
          </div>

          {summaryState.result.action_items.length > 0 && (
            <ul className="list-disc list-inside text-slate-600 space-y-0.5">
              {summaryState.result.action_items.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          )}

          {summaryState.result.suggested_follow_up && (
            <div className="text-indigo-700">
              <span className="font-medium">Suggested follow-up:</span> {summaryState.result.suggested_follow_up}
            </div>
          )}

          <div className="text-slate-400 flex items-center gap-2">
            <span>{summaryState.result.disclaimer}</span>
            {summaryState.result.language && (
              <span className="uppercase">· {summaryState.result.language}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
