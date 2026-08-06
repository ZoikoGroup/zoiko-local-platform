"use client";

import { useEffect, useState } from "react";
import {
  listMyNotifications,
  getNotificationPreferences,
  updateNotificationPreferences,
  ApiError,
  type NotificationDelivery,
  type NotificationPreferences,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

export default function NotificationsPage() {
  const [notifications, setNotifications] = useState<NotificationDelivery[]>([]);
  const [notificationsLoading, setNotificationsLoading] = useState(true);
  const [notificationsError, setNotificationsError] = useState<string | null>(null);

  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  const [prefsLoading, setPrefsLoading] = useState(true);
  const [prefsSaving, setPrefsSaving] = useState(false);
  const [prefsError, setPrefsError] = useState<string | null>(null);
  const [quietHoursEnabled, setQuietHoursEnabled] = useState(false);
  const [quietHoursDraft, setQuietHoursDraft] = useState({ start: "22:00", end: "07:00", timezone: "UTC" });

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    listMyNotifications(token)
      .then(setNotifications)
      .catch(() => setNotificationsError("Couldn't load communications history."))
      .finally(() => setNotificationsLoading(false));
    getNotificationPreferences(token)
      .then((p) => {
        setPrefs(p);
        setQuietHoursEnabled(p.quiet_hours_start !== null && p.quiet_hours_end !== null);
        setQuietHoursDraft({
          start: (p.quiet_hours_start ?? "22:00:00").slice(0, 5),
          end: (p.quiet_hours_end ?? "07:00:00").slice(0, 5),
          timezone: p.quiet_hours_timezone,
        });
      })
      .catch(() => setPrefsError("Couldn't load notification preferences."))
      .finally(() => setPrefsLoading(false));
  }, []);

  async function handleUpdatePrefs(patch: Partial<NotificationPreferences>) {
    const token = getToken();
    if (!token) return;
    setPrefsSaving(true);
    setPrefsError(null);
    try {
      setPrefs(await updateNotificationPreferences(token, patch));
    } catch (err) {
      setPrefsError(err instanceof ApiError ? err.message : "Couldn't save notification preferences.");
    } finally {
      setPrefsSaving(false);
    }
  }

  function handleToggleQuietHours(enabled: boolean) {
    setQuietHoursEnabled(enabled);
    if (enabled) {
      handleUpdatePrefs({
        quiet_hours_start: `${quietHoursDraft.start}:00`,
        quiet_hours_end: `${quietHoursDraft.end}:00`,
        quiet_hours_timezone: quietHoursDraft.timezone,
      });
    } else {
      handleUpdatePrefs({ quiet_hours_start: null, quiet_hours_end: null });
    }
  }

  function handleSaveQuietHoursWindow() {
    handleUpdatePrefs({
      quiet_hours_start: `${quietHoursDraft.start}:00`,
      quiet_hours_end: `${quietHoursDraft.end}:00`,
      quiet_hours_timezone: quietHoursDraft.timezone,
    });
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Notifications</h2>
        <p className="text-sm text-slate-500">How and when Zoiko Local reaches you, and what&apos;s been sent.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Notification Preferences</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Security and account-access notices always send — these controls only apply to everything else.
          </p>
        </div>

        {prefsLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {prefsError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{prefsError}</p>}

        {!prefsLoading && prefs && (
          <div className="space-y-4">
            <label className="flex items-center justify-between gap-4">
              <span className="text-sm text-slate-700">Email updates (number activity, verification results, etc.)</span>
              <input
                type="checkbox"
                checked={prefs.transactional_enabled}
                disabled={prefsSaving}
                onChange={(e) => handleUpdatePrefs({ transactional_enabled: e.target.checked })}
              />
            </label>
            <label className="flex items-center justify-between gap-4">
              <span className="text-sm text-slate-700">SMS notifications</span>
              <input
                type="checkbox"
                checked={prefs.sms_enabled}
                disabled={prefsSaving}
                onChange={(e) => handleUpdatePrefs({ sms_enabled: e.target.checked })}
              />
            </label>

            <div className="pt-2 border-t border-slate-100">
              <label className="flex items-center justify-between gap-4">
                <span className="text-sm text-slate-700">Quiet hours (holds SMS until the window ends)</span>
                <input
                  type="checkbox"
                  checked={quietHoursEnabled}
                  disabled={prefsSaving}
                  onChange={(e) => handleToggleQuietHours(e.target.checked)}
                />
              </label>

              {quietHoursEnabled && (
                <div className="flex items-center gap-2 flex-wrap mt-3">
                  <input
                    type="time"
                    value={quietHoursDraft.start}
                    onChange={(e) => setQuietHoursDraft((d) => ({ ...d, start: e.target.value }))}
                    className="text-sm rounded-lg border border-slate-200 px-2 py-1.5"
                  />
                  <span className="text-sm text-slate-500">to</span>
                  <input
                    type="time"
                    value={quietHoursDraft.end}
                    onChange={(e) => setQuietHoursDraft((d) => ({ ...d, end: e.target.value }))}
                    className="text-sm rounded-lg border border-slate-200 px-2 py-1.5"
                  />
                  <input
                    value={quietHoursDraft.timezone}
                    onChange={(e) => setQuietHoursDraft((d) => ({ ...d, timezone: e.target.value }))}
                    placeholder="IANA timezone, e.g. America/New_York"
                    className="text-sm rounded-lg border border-slate-200 px-2 py-1.5 w-52"
                  />
                  <button
                    onClick={handleSaveQuietHoursWindow}
                    disabled={prefsSaving}
                    className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                  >
                    Save
                  </button>
                </div>
              )}
            </div>
          </div>
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
                    n.status === "sent"
                      ? "bg-emerald-50 text-emerald-700"
                      : n.status === "suppressed"
                        ? "bg-slate-100 text-slate-600"
                        : "bg-red-50 text-red-700"
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
