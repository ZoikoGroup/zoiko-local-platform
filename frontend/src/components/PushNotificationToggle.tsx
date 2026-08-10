"use client";

import { useEffect, useState } from "react";
import { subscribeToPush, unsubscribeFromPush } from "@/lib/api";
import { getToken } from "@/lib/auth";

const VAPID_PUBLIC_KEY = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY ?? "";

function urlBase64ToUint8Array(base64Url: string): Uint8Array {
  const padding = "=".repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

type Status = "unsupported" | "loading" | "disabled" | "enabled" | "denied";

function initialStatus(): Status {
  if (typeof window === "undefined") return "loading";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !VAPID_PUBLIC_KEY) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  return "loading";
}

export default function PushNotificationToggle() {
  const [status, setStatus] = useState<Status>(initialStatus);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status !== "loading") return;

    navigator.serviceWorker
      .register("/sw.js")
      .then((registration) => registration.pushManager.getSubscription())
      .then((subscription) => setStatus(subscription ? "enabled" : "disabled"))
      .catch(() => setStatus("disabled"));
  }, [status]);

  async function handleEnable() {
    setBusy(true);
    setError(null);
    try {
      const registration = await navigator.serviceWorker.ready;
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setStatus("denied");
        return;
      }

      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY) as BufferSource,
      });
      const json = subscription.toJSON();

      const token = getToken();
      if (!token) return;
      await subscribeToPush(token, {
        endpoint: subscription.endpoint,
        p256dh: json.keys?.p256dh ?? "",
        auth: json.keys?.auth ?? "",
      });
      setStatus("enabled");
    } catch {
      setError("Couldn't enable push notifications.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable() {
    setBusy(true);
    setError(null);
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        const token = getToken();
        if (token) await unsubscribeFromPush(token, subscription.endpoint);
        await subscription.unsubscribe();
      }
      setStatus("disabled");
    } catch {
      setError("Couldn't disable push notifications.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
      <div>
        <h3 className="font-semibold text-slate-900">Push Notifications</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Get browser notifications for account activity, even when this tab is closed.
        </p>
      </div>

      {status === "unsupported" && (
        <p className="text-sm text-slate-500">Not supported in this browser.</p>
      )}
      {status === "loading" && <p className="text-sm text-slate-500">Loading...</p>}
      {status === "denied" && (
        <p className="text-sm text-amber-700">
          Blocked at the browser level. Enable notifications for this site in your browser settings to turn this on.
        </p>
      )}
      {status === "disabled" && (
        <button
          onClick={handleEnable}
          disabled={busy}
          className="text-sm font-medium bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-lg px-4 py-2 transition"
        >
          {busy ? "Enabling..." : "Enable push notifications"}
        </button>
      )}
      {status === "enabled" && (
        <div className="flex items-center gap-3">
          <span className="text-sm text-emerald-700 font-medium">Enabled on this device</span>
          <button
            onClick={handleDisable}
            disabled={busy}
            className="text-sm font-medium text-slate-500 hover:text-slate-700 disabled:opacity-50 transition"
          >
            {busy ? "Disabling..." : "Disable"}
          </button>
        </div>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  );
}
