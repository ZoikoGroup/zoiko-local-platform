"use client";

import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import {
  getCurrentUser,
  mfaSetup,
  mfaEnable,
  mfaDisable,
  listMyComplianceCases,
  startKycVerification,
  listRetentionPolicies,
  setRetentionPolicy,
  listMyNotifications,
  ApiError,
  type User,
  type MyComplianceCase,
  type RetentionPolicies,
  type NotificationDelivery,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

type SetupState = { secret: string; otpauth_uri: string } | null;

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-amber-50 text-amber-700",
  approved: "bg-emerald-50 text-emerald-700",
  rejected: "bg-red-50 text-red-700",
  expired: "bg-slate-100 text-slate-600",
};

const RETENTION_LABELS: Record<keyof RetentionPolicies, string> = {
  voicemail: "Voicemail recordings",
  call_recording: "Call recordings",
  video_recording: "Video call recordings",
};

export default function SettingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [cases, setCases] = useState<MyComplianceCase[]>([]);
  const [casesLoading, setCasesLoading] = useState(true);
  const [verifyingCaseId, setVerifyingCaseId] = useState<string | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  const [setupState, setSetupState] = useState<SetupState>(null);
  const [code, setCode] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [showDisable, setShowDisable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [retention, setRetention] = useState<RetentionPolicies | null>(null);
  const [retentionLoading, setRetentionLoading] = useState(true);
  const [retentionError, setRetentionError] = useState<string | null>(null);
  const [retentionDrafts, setRetentionDrafts] = useState<Partial<Record<keyof RetentionPolicies, string>>>({});
  const [retentionSavingKey, setRetentionSavingKey] = useState<keyof RetentionPolicies | null>(null);

  const [notifications, setNotifications] = useState<NotificationDelivery[]>([]);
  const [notificationsLoading, setNotificationsLoading] = useState(true);
  const [notificationsError, setNotificationsError] = useState<string | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    getCurrentUser(token)
      .then(setUser)
      .finally(() => setLoading(false));
    listMyComplianceCases(token)
      .then(setCases)
      .finally(() => setCasesLoading(false));
    listRetentionPolicies(token)
      .then(setRetention)
      .catch(() => setRetentionError("Couldn't load retention settings."))
      .finally(() => setRetentionLoading(false));
    listMyNotifications(token)
      .then(setNotifications)
      .catch(() => setNotificationsError("Couldn't load communications history."))
      .finally(() => setNotificationsLoading(false));
  }, []);

  async function handleStartVerification(caseId: string) {
    const token = getToken();
    if (!token) return;
    setVerifyingCaseId(caseId);
    setVerifyError(null);
    try {
      const { inquiry_id, verification_url } = await startKycVerification(token, caseId);
      setCases((prev) =>
        prev.map((c) => (c.id === caseId ? { ...c, status: "pending", kyc_inquiry_id: inquiry_id } : c))
      );
      window.open(verification_url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setVerifyError(err instanceof ApiError ? err.message : "Couldn't start identity verification.");
    } finally {
      setVerifyingCaseId(null);
    }
  }

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

  async function refreshUser() {
    const token = getToken();
    if (!token) return;
    setUser(await getCurrentUser(token));
  }

  async function handleStartSetup() {
    const token = getToken();
    if (!token) return;
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      setSetupState(await mfaSetup(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start MFA setup");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmEnable(e: React.FormEvent) {
    e.preventDefault();
    const token = getToken();
    if (!token) return;
    setError(null);
    setBusy(true);
    try {
      await mfaEnable(token, code);
      setSetupState(null);
      setCode("");
      setMessage("Two-factor authentication is now enabled.");
      await refreshUser();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Invalid code");
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable(e: React.FormEvent) {
    e.preventDefault();
    const token = getToken();
    if (!token) return;
    setError(null);
    setBusy(true);
    try {
      await mfaDisable(token, disableCode);
      setShowDisable(false);
      setDisableCode("");
      setMessage("Two-factor authentication has been turned off.");
      await refreshUser();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Invalid code");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="text-sm text-slate-500">Loading...</div>;
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Settings</h2>
        <p className="text-sm text-slate-500">Account and security preferences.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Security</h3>

        {message && (
          <p className="text-sm text-emerald-700 bg-emerald-50 rounded-lg px-3 py-2">{message}</p>
        )}
        {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-medium text-slate-800">Two-factor authentication</div>
            <div className="text-xs text-slate-500 mt-0.5">
              {user?.mfa_enabled
                ? "Enabled — a code from your authenticator app is required at login."
                : "Not enabled. Add an extra layer of security to your account."}
            </div>
          </div>
          {user?.mfa_enabled ? (
            <span className="text-xs font-medium text-emerald-700 bg-emerald-50 rounded-full px-3 py-1">
              ON
            </span>
          ) : (
            <span className="text-xs font-medium text-slate-500 bg-slate-100 rounded-full px-3 py-1">
              OFF
            </span>
          )}
        </div>

        {!user?.mfa_enabled && !setupState && (
          <button
            onClick={handleStartSetup}
            disabled={busy}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2 transition"
          >
            Enable two-factor authentication
          </button>
        )}

        {setupState && (
          <form onSubmit={handleConfirmEnable} className="space-y-4 border-t border-slate-100 pt-4">
            <p className="text-sm text-slate-600">
              Scan this QR code with an authenticator app (Google Authenticator, Authy, etc.),
              then enter the 6-digit code it shows.
            </p>
            <div className="flex justify-center bg-white p-3 border border-slate-200 rounded-lg w-fit mx-auto">
              <QRCodeSVG value={setupState.otpauth_uri} size={160} />
            </div>
            <p className="text-xs text-slate-400 text-center break-all">
              Can&apos;t scan? Enter manually: <span className="font-mono">{setupState.secret}</span>
            </p>
            <input
              type="text"
              inputMode="numeric"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-center text-lg tracking-[0.5em] font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
              placeholder="000000"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy || code.length !== 6}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg py-2 transition"
              >
                Confirm & enable
              </button>
              <button
                type="button"
                onClick={() => setSetupState(null)}
                className="text-sm text-slate-500 hover:text-slate-700 px-3"
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {user?.mfa_enabled && !showDisable && (
          <button
            onClick={() => setShowDisable(true)}
            className="text-sm text-red-600 hover:text-red-700 font-medium"
          >
            Turn off two-factor authentication
          </button>
        )}

        {showDisable && (
          <form onSubmit={handleDisable} className="space-y-3 border-t border-slate-100 pt-4">
            <p className="text-sm text-slate-600">Enter a current code to confirm turning this off.</p>
            <input
              type="text"
              inputMode="numeric"
              maxLength={6}
              required
              value={disableCode}
              onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, ""))}
              className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-center text-lg tracking-[0.5em] font-mono focus:outline-none focus:ring-2 focus:ring-red-500/40 focus:border-red-500 transition"
              placeholder="000000"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy || disableCode.length !== 6}
                className="flex-1 bg-red-600 hover:bg-red-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg py-2 transition"
              >
                Confirm turn off
              </button>
              <button
                type="button"
                onClick={() => setShowDisable(false)}
                className="text-sm text-slate-500 hover:text-slate-700 px-3"
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Identity verification</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Status of any ID verification requests on your account.
          </p>
        </div>

        {casesLoading && <p className="text-sm text-slate-500">Loading...</p>}

        {verifyError && (
          <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{verifyError}</p>
        )}

        {!casesLoading && cases.length === 0 && (
          <p className="text-sm text-slate-500">
            No verification requests yet — these appear here when a number purchase requires ID
            checks for that country.
          </p>
        )}

        {!casesLoading && cases.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {cases.map((c) => (
              <li key={c.id} className="py-3 flex items-center justify-between gap-4">
                <div>
                  <div className="text-sm font-medium text-slate-800">
                    {c.jurisdiction} — {c.requirement_type.replaceAll("_", " ")}
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    {c.documents.length} document{c.documents.length === 1 ? "" : "s"} submitted ·{" "}
                    {new Date(c.created_at).toLocaleDateString()}
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  {(c.status === "pending" || c.status === "rejected") && (
                    <button
                      onClick={() => handleStartVerification(c.id)}
                      disabled={verifyingCaseId === c.id}
                      className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                    >
                      {verifyingCaseId === c.id
                        ? "Starting…"
                        : c.status === "rejected"
                          ? "Try again"
                          : c.kyc_inquiry_id
                            ? "Continue verification"
                            : "Verify identity"}
                    </button>
                  )}
                  <span
                    className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize ${
                      STATUS_STYLES[c.status] ?? "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {c.status}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
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

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Communications History</h3>
          <p className="text-xs text-slate-500 mt-0.5">Emails sent to your account.</p>
        </div>

        {notificationsLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {notificationsError && <p className="text-sm text-red-600">{notificationsError}</p>}

        {!notificationsLoading && notifications.length === 0 && (
          <p className="text-sm text-slate-500">No emails sent yet.</p>
        )}

        {!notificationsLoading && notifications.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {notifications.map((n) => (
              <li key={n.id} className="py-3 flex items-center justify-between gap-4">
                <div>
                  <div className="text-sm font-medium text-slate-800">{n.subject}</div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    {n.recipient_email} · {new Date(n.created_at).toLocaleString()}
                  </div>
                </div>
                <span
                  className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize shrink-0 ${
                    n.status === "sent" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"
                  }`}
                  title={n.error ?? undefined}
                >
                  {n.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
