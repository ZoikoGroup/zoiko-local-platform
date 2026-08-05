"use client";

import { useEffect, useRef, useState } from "react";
import {
  listMyNotifications,
  markNotificationRead,
  markAllNotificationsRead,
  type NotificationDelivery,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const POLL_INTERVAL_MS = 30_000;

export default function NotificationBell() {
  const [token] = useState<string | null>(() => getToken());
  const [notifications, setNotifications] = useState<NotificationDelivery[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const unreadCount = notifications.filter((n) => !n.read_at).length;

  useEffect(() => {
    if (!token) return;
    function load() {
      listMyNotifications(token as string)
        .then((data) => {
          setNotifications(data);
          setError(null);
        })
        .catch(() => setError("Couldn't load notifications."));
    }
    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [token]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function handleOpen() {
    setOpen((prev) => !prev);
  }

  async function handleMarkRead(id: string) {
    if (!token) return;
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read_at: new Date().toISOString() } : n)));
    try {
      await markNotificationRead(token, id);
    } catch {
      // best-effort - a poll refresh within 30s will correct any drift
    }
  }

  async function handleMarkAllRead() {
    if (!token) return;
    const now = new Date().toISOString();
    setNotifications((prev) => prev.map((n) => ({ ...n, read_at: n.read_at ?? now })));
    try {
      await markAllNotificationsRead(token);
    } catch {
      // best-effort - a poll refresh within 30s will correct any drift
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={handleOpen}
        className="relative w-9 h-9 rounded-lg flex items-center justify-center text-slate-500 hover:bg-slate-100 hover:text-slate-700 transition"
        aria-label="Notifications"
      >
        <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2a2 2 0 0 1-.6 1.4L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9"
          />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute top-1 right-1 min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-semibold flex items-center justify-center">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 bg-white rounded-xl border border-slate-200 shadow-lg z-20 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
            <span className="text-sm font-semibold text-slate-900">Notifications</span>
            {unreadCount > 0 && (
              <button
                onClick={handleMarkAllRead}
                className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
              >
                Mark all read
              </button>
            )}
          </div>

          {error && <p className="text-xs text-red-600 px-4 py-3">{error}</p>}

          {notifications.length === 0 && !error && (
            <p className="text-sm text-slate-600 px-4 py-6 text-center">No notifications yet.</p>
          )}

          <ul className="max-h-96 overflow-y-auto divide-y divide-slate-100">
            {notifications.slice(0, 20).map((n) => (
              <li
                key={n.id}
                role={!n.read_at ? "button" : undefined}
                tabIndex={!n.read_at ? 0 : undefined}
                onClick={() => !n.read_at && handleMarkRead(n.id)}
                onKeyDown={(e) => {
                  if (!n.read_at && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault();
                    handleMarkRead(n.id);
                  }
                }}
                className={`px-4 py-3 text-sm transition ${
                  n.read_at ? "bg-white" : "bg-indigo-50/60 hover:bg-indigo-50 cursor-pointer"
                }`}
              >
                <div className="flex items-start gap-2">
                  {!n.read_at && <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-indigo-600 shrink-0" />}
                  <div className={n.read_at ? "pl-3.5" : ""}>
                    <p className="text-slate-800 leading-snug">{n.subject}</p>
                    <div className="flex items-center gap-2 mt-1">
                      {n.status === "failed" && (
                        <span className="text-[10px] font-medium text-red-600 uppercase">Delivery failed</span>
                      )}
                      {n.status === "suppressed" && (
                        <span className="text-[10px] font-medium text-slate-500 uppercase">Not sent (your preferences)</span>
                      )}
                      <span className="text-[10px] text-slate-600">
                        {new Date(n.created_at).toLocaleString()}
                      </span>
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
