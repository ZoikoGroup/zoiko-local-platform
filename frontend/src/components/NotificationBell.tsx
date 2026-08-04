"use client";

import { useEffect, useRef, useState } from "react";
import {
  getUnreadNotificationCount,
  listMyNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationDelivery,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const POLL_INTERVAL_MS = 30_000;

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState<NotificationDelivery[]>([]);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;

    function refreshUnreadCount() {
      getUnreadNotificationCount(token!)
        .then((res) => setUnreadCount(res.unread_count))
        .catch(() => {});
    }

    refreshUnreadCount();
    const interval = setInterval(refreshUnreadCount, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function toggleOpen() {
    const next = !open;
    setOpen(next);
    if (next) {
      const token = getToken();
      if (!token) return;
      setLoading(true);
      listMyNotifications(token)
        .then((all) => setNotifications(all.slice(0, 10)))
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }

  function handleMarkRead(id: string) {
    const token = getToken();
    if (!token) return;
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read_at: new Date().toISOString() } : n)));
    setUnreadCount((prev) => Math.max(0, prev - 1));
    markNotificationRead(token, id).catch(() => {});
  }

  function handleMarkAllRead() {
    const token = getToken();
    if (!token) return;
    setNotifications((prev) => prev.map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })));
    setUnreadCount(0);
    markAllNotificationsRead(token).catch(() => {});
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={toggleOpen}
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
          <span className="absolute top-1 right-1 min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-semibold leading-4 text-center">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 max-h-96 overflow-y-auto bg-white rounded-xl border border-slate-200 shadow-lg z-20">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
            <h3 className="text-sm font-semibold text-slate-900">Notifications</h3>
            {unreadCount > 0 && (
              <button
                onClick={handleMarkAllRead}
                className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
              >
                Mark all as read
              </button>
            )}
          </div>

          {loading && <p className="px-4 py-6 text-sm text-slate-500 text-center">Loading...</p>}

          {!loading && notifications.length === 0 && (
            <p className="px-4 py-6 text-sm text-slate-500 text-center">No notifications yet.</p>
          )}

          {!loading && notifications.length > 0 && (
            <ul className="divide-y divide-slate-100">
              {notifications.map((n) => (
                <li
                  key={n.id}
                  onClick={() => !n.read_at && handleMarkRead(n.id)}
                  className={`px-4 py-3 cursor-pointer transition ${
                    n.read_at ? "bg-white" : "bg-indigo-50/60 hover:bg-indigo-50"
                  }`}
                >
                  <div className="flex items-start gap-2">
                    {!n.read_at && <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-indigo-600 shrink-0" />}
                    <div className={n.read_at ? "pl-3.5" : ""}>
                      <div className="text-sm font-medium text-slate-800">{n.subject}</div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        {new Date(n.created_at).toLocaleString()}
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
