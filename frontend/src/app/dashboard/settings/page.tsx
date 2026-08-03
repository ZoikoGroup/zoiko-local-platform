"use client";

import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import {
  getCurrentUser,
  mfaSetup,
  mfaEnable,
  mfaDisable,
  listMyComplianceCases,
  listMyAuditEvents,
  ApiError,
  type User,
  type MyComplianceCase,
  type AuditEvent,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

type SetupState = { secret: string; otpauth_uri: string } | null;

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-amber-50 text-amber-700",
  approved: "bg-emerald-50 text-emerald-700",
  rejected: "bg-red-50 text-red-700",
  expired: "bg-slate-100 text-slate-600",
};

export default function SettingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [cases, setCases] = useState<MyComplianceCase[]>([]);
  const [casesLoading, setCasesLoading] = useState(true);

  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [auditLoading, setAuditLoading] = useState(true);
  const [auditForbidden, setAuditForbidden] = useState(false);

  const [setupState, setSetupState] = useState<SetupState>(null);
  const [code, setCode] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [showDisable, setShowDisable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    getCurrentUser(token)
      .then(setUser)
      .finally(() => setLoading(false));
    listMyComplianceCases(token)
      .then(setCases)
      .finally(() => setCasesLoading(false));
    listMyAuditEvents(token)
      .then(setAuditEvents)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 403) setAuditForbidden(true);
      })
      .finally(() => setAuditLoading(false));
  }, []);

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
                <span
                  className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize shrink-0 ${
                    STATUS_STYLES[c.status] ?? "bg-slate-100 text-slate-600"
                  }`}
                >
                  {c.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
