"use client";

import { useEffect, useState } from "react";
import {
  getCurrentUser,
  setPhoneNumber,
  listRetentionPolicies,
  setRetentionPolicy,
  ApiError,
  type User,
  type RetentionPolicies,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const RETENTION_LABELS: Record<keyof RetentionPolicies, string> = {
  voicemail: "Voicemail recordings",
  call_recording: "Call recordings",
  video_recording: "Video call recordings",
};

export default function SettingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const [retention, setRetention] = useState<RetentionPolicies | null>(null);
  const [retentionLoading, setRetentionLoading] = useState(true);
  const [retentionError, setRetentionError] = useState<string | null>(null);
  const [retentionDrafts, setRetentionDrafts] = useState<Partial<Record<keyof RetentionPolicies, string>>>({});
  const [retentionSavingKey, setRetentionSavingKey] = useState<keyof RetentionPolicies | null>(null);

  const [phoneDraft, setPhoneDraft] = useState("");
  const [phoneBusy, setPhoneBusy] = useState(false);
  const [phoneError, setPhoneError] = useState<string | null>(null);
  const [phoneMessage, setPhoneMessage] = useState<string | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    getCurrentUser(token)
      .then((u) => {
        setUser(u);
        setPhoneDraft(u.phone_number ?? "");
      })
      .finally(() => setLoading(false));
    listRetentionPolicies(token)
      .then(setRetention)
      .catch(() => setRetentionError("Couldn't load retention settings."))
      .finally(() => setRetentionLoading(false));
  }, []);

  async function handleSaveRetention(artifactType: keyof RetentionPolicies) {
    const token = getToken();
    const draft = retentionDrafts[artifactType];
    if (!token || !draft) return;
    const days = Number(draft);
    if (!Number.isInteger(days) || days < 1) {
      setRetentionError("Retention must be a whole number of days, at least 1.");
      return;
    }
    setRetentionSavingKey(artifactType);
    setRetentionError(null);
    try {
      await setRetentionPolicy(token, artifactType, days);
      setRetention((prev) => (prev ? { ...prev, [artifactType]: days } : prev));
      setRetentionDrafts((prev) => ({ ...prev, [artifactType]: undefined }));
    } catch (err) {
      setRetentionError(err instanceof ApiError ? err.message : "Couldn't save retention setting.");
    } finally {
      setRetentionSavingKey(null);
    }
  }

  async function handleSavePhone() {
    const token = getToken();
    if (!token) return;
    setPhoneBusy(true);
    setPhoneError(null);
    setPhoneMessage(null);
    try {
      const updated = await setPhoneNumber(token, phoneDraft || null);
      setUser(updated);
      setPhoneMessage("Phone number saved.");
    } catch (err) {
      setPhoneError(err instanceof ApiError ? err.message : "Couldn't save phone number.");
    } finally {
      setPhoneBusy(false);
    }
  }

  if (loading) {
    return <div className="text-sm text-slate-500">Loading...</div>;
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Settings</h2>
        <p className="text-sm text-slate-500">Profile and data retention.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Phone Number</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Used for SMS alerts on safety-critical events (e.g. a number being suspended). Optional.
          </p>
        </div>
        {phoneError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{phoneError}</p>}
        {phoneMessage && (
          <p className="text-sm text-emerald-700 bg-emerald-50 rounded-lg px-3 py-2">{phoneMessage}</p>
        )}
        <div className="flex gap-2">
          <input
            type="tel"
            value={phoneDraft}
            onChange={(e) => setPhoneDraft(e.target.value)}
            placeholder="+15551234567"
            className="flex-1 rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
          />
          <button
            onClick={handleSavePhone}
            disabled={phoneBusy || phoneDraft === (user?.phone_number ?? "")}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2 transition"
          >
            {phoneBusy ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Data Retention</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            How long recordings are kept before they&apos;re permanently deleted from storage.
            {user?.role === "member" && " Only an account Owner or Admin can change these."}
          </p>
        </div>

        {retentionLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {retentionError && (
          <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{retentionError}</p>
        )}

        {retention && (
          <ul className="divide-y divide-slate-100">
            {(Object.keys(RETENTION_LABELS) as (keyof RetentionPolicies)[]).map((key) => (
              <li key={key} className="py-3 flex items-center justify-between gap-4">
                <span className="text-sm font-medium text-slate-800">{RETENTION_LABELS[key]}</span>
                {user?.role === "member" ? (
                  <span className="text-sm text-slate-600">{retention[key]} days</span>
                ) : (
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={1}
                      aria-label={`${RETENTION_LABELS[key]} retention in days`}
                      value={retentionDrafts[key] ?? retention[key]}
                      onChange={(e) =>
                        setRetentionDrafts((prev) => ({ ...prev, [key]: e.target.value }))
                      }
                      className="w-20 rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-right"
                    />
                    <span className="text-sm text-slate-500">days</span>
                    <button
                      onClick={() => handleSaveRetention(key)}
                      disabled={
                        retentionSavingKey === key ||
                        retentionDrafts[key] === undefined ||
                        retentionDrafts[key] === String(retention[key])
                      }
                      className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-40 disabled:hover:text-indigo-600"
                    >
                      {retentionSavingKey === key ? "Saving..." : "Save"}
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
