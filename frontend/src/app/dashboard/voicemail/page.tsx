"use client";

import { useEffect, useState, useCallback } from "react";
import {
  listVoicemails,
  summarizeVoicemail,
  grantAiProcessingConsent,
  ApiError,
  type VoicemailEntry,
} from "@/lib/api";
import { getToken } from "@/lib/auth";
import { CallRow, type SummaryKey, type SummaryState } from "@/components/CallRow";

export default function VoicemailPage() {
  const [token] = useState<string | null>(() => getToken());

  const [voicemails, setVoicemails] = useState<VoicemailEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [summaries, setSummaries] = useState<Record<SummaryKey, SummaryState>>({});

  const loadAll = useCallback(() => {
    if (!token) return;
    return listVoicemails(token)
      .then((data) => {
        setVoicemails(data);
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load voicemail."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function handleSummarize(id: string) {
    if (!token) return;
    const key: SummaryKey = `voicemail:${id}`;
    setSummaries((prev) => ({ ...prev, [key]: { status: "busy" } }));
    try {
      const result = await summarizeVoicemail(token, id);
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
      const key: SummaryKey = `voicemail:${id}`;
      setSummaries((prev) => ({ ...prev, [key]: { status: "error", message: "Couldn't grant AI consent." } }));
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Voicemail</h2>
        <p className="text-sm text-slate-500">Messages left across all your numbers, with AI-generated summaries.</p>
      </div>

      {loadError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{loadError}</p>}

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {!loading && voicemails.length === 0 && <p className="text-sm text-slate-500">No voicemails yet.</p>}

        <div className="space-y-3">
          {voicemails.map((v) => (
            <CallRow
              key={v.id}
              label={`From ${v.from}`}
              status="left a message"
              duration={v.duration}
              createdAt={v.created_at}
              recordingUrl={v.recording_url}
              summaryState={summaries[`voicemail:${v.id}`] ?? { status: "idle" }}
              onSummarize={() => handleSummarize(v.id)}
              onGrantConsent={() => handleGrantConsent(v.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
