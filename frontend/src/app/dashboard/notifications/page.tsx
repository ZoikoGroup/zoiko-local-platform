"use client";

import { useEffect, useState } from "react";
import {
  listMyNotifications,
  getNotificationPreferences,
  updateNotificationPreferences,
  subscribeToPush,
  unsubscribeFromPush,
  getVapidPublicKey,
  ApiError,
  type NotificationDelivery,
  type NotificationPreferences,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

// Standard VAPID-key decoding for PushManager.subscribe's applicationServerKey -
// browsers require a Uint8Array, but the key is handed out as a URL-safe base64 string.
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

// The domains actually seeded in notification_templates today (Email
// Communications System doc's full taxonomy has more - ROUTE/MSG/DEVICE/
// TRUST/SUP/OPS/PART/MKTG aren't imported yet, so there's nothing to opt
// out of there). Kept in sync manually with the canonical estate seed
// migrations, not fetched from the API, since the set changes rarely.
const NOTIFICATION_DOMAINS: { key: string; label: string }[] = [
  { key: "AUTH", label: "Account & security" },
  { key: "ORG", label: "Organization & team" },
  { key: "NUM", label: "Numbers" },
  { key: "PORT", label: "Number porting" },
  { key: "COMP", label: "Compliance & emergency calling" },
  { key: "VOICE", label: "Calling & voicemail" },
  { key: "BILL", label: "Billing & plans" },
  { key: "INTG", label: "APIs & integrations" },
];

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

  const [pushSupported, setPushSupported] = useState(false);
  const [pushSubscribed, setPushSubscribed] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushError, setPushError] = useState<string | null>(null);

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

  useEffect(() => {
    if (typeof window === "undefined" || !("serviceWorker" in navigator) || !("PushManager" in window)) return;
    Promise.resolve()
      .then(() => {
        setPushSupported(true);
        return navigator.serviceWorker.register("/sw.js");
      })
      .then((registration) => registration.pushManager.getSubscription())
      .then((subscription) => setPushSubscribed(subscription !== null))
      .catch(() => {});
  }, []);

  async function handleEnablePush() {
    const token = getToken();
    if (!token) return;
    setPushError(null);
    setPushBusy(true);
    try {
      const vapidKey = getVapidPublicKey();
      if (!vapidKey) {
        setPushError("Push notifications aren't configured on this server yet.");
        return;
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setPushError("Browser notification permission was denied.");
        return;
      }
      await navigator.serviceWorker.register("/sw.js");
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey) as BufferSource,
      });
      const json = subscription.toJSON();
      if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
        throw new Error("Browser did not return a usable push subscription.");
      }
      await subscribeToPush(token, { endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth });
      setPushSubscribed(true);
    } catch (err) {
      setPushError(err instanceof ApiError ? err.message : "Couldn't enable push notifications.");
    } finally {
      setPushBusy(false);
    }
  }

  async function handleDisablePush() {
    const token = getToken();
    if (!token) return;
    setPushError(null);
    setPushBusy(true);
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await unsubscribeFromPush(token, subscription.endpoint);
        await subscription.unsubscribe();
      }
      setPushSubscribed(false);
    } catch {
      setPushError("Couldn't disable push notifications.");
    } finally {
      setPushBusy(false);
    }
  }

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

  function handleToggleDomain(domain: string, enabled: boolean) {
    if (!prefs) return;
    const current = prefs.disabled_domains ?? [];
    const next = enabled ? current.filter((d) => d !== domain) : [...current, domain];
    handleUpdatePrefs({ disabled_domains: next });
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

            <label className="flex items-center justify-between gap-4">
              <span className="text-sm text-slate-700">
                Push notifications (this browser)
                {!pushSupported && <span className="block text-xs text-slate-400">Not supported in this browser.</span>}
              </span>
              <input
                type="checkbox"
                checked={pushSubscribed}
                disabled={!pushSupported || pushBusy}
                onChange={(e) => (e.target.checked ? handleEnablePush() : handleDisablePush())}
              />
            </label>
            {pushError && <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">{pushError}</p>}

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

            <div className="pt-3 border-t border-slate-100">
              <p className="text-sm text-slate-700 mb-1">Email categories</p>
              <p className="text-xs text-slate-500 mb-3">
                Turn off specific categories without affecting the rest — security and account-access notices
                always send regardless of these.
              </p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {NOTIFICATION_DOMAINS.map((domain) => (
                  <label key={domain.key} className="flex items-center justify-between gap-2">
                    <span className="text-sm text-slate-700">{domain.label}</span>
                    <input
                      type="checkbox"
                      checked={!(prefs.disabled_domains ?? []).includes(domain.key)}
                      disabled={prefsSaving}
                      onChange={(e) => handleToggleDomain(domain.key, e.target.checked)}
                    />
                  </label>
                ))}
              </div>
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
