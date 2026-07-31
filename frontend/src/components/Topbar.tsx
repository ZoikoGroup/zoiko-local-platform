"use client";

import { useRouter, usePathname } from "next/navigation";
import { clearToken } from "@/lib/auth";
import { currentPageLabel } from "@/lib/nav";
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

        <button
          className="w-9 h-9 rounded-lg flex items-center justify-center text-slate-500 hover:bg-slate-100 hover:text-slate-700 transition"
          aria-label="Notifications"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 1 0-12 0v3.2a2 2 0 0 1-.6 1.4L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9"
            />
          </svg>
        </button>

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
            className="text-xs text-slate-400 hover:text-slate-700 font-medium ml-1 transition"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
