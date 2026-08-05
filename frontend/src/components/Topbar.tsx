"use client";

import { useRouter, usePathname } from "next/navigation";
import { clearToken } from "@/lib/auth";
import { currentPageLabel } from "@/lib/nav";
import NotificationBell from "@/components/NotificationBell";
import type { User } from "@/lib/api";

export default function Topbar({ user }: { user: User | null }) {
  const router = useRouter();
  const pathname = usePathname();

  function handleLogout() {
    clearToken();
    router.push("/login");
  }

  const initial = user?.email?.[0]?.toUpperCase() ?? "?";

  return (
    <header className="h-16 border-b border-slate-200 bg-white/80 backdrop-blur flex items-center justify-between px-6 sticky top-0 z-10">
      <h1 className="text-lg font-semibold text-slate-900">{currentPageLabel(pathname)}</h1>

      <div className="flex items-center gap-3">
        <button className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg pl-3 pr-4 py-2 transition shadow-sm shadow-indigo-600/20">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path strokeLinecap="round" d="M12 5v14M5 12h14" />
          </svg>
          Buy Number
        </button>

        <NotificationBell />

        <div className="h-8 w-px bg-slate-200" />

        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-indigo-700 flex items-center justify-center text-xs font-semibold text-white shadow-sm">
            {initial}
          </div>
          <div className="text-sm leading-tight">
            <div className="font-medium text-slate-900">
              {user?.email ?? "Loading..."}
            </div>
            <div className="text-xs text-slate-500 capitalize">{user?.role}</div>
          </div>
          <button
            onClick={handleLogout}
            className="text-xs text-slate-600 hover:text-slate-700 font-medium ml-1 transition"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
