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

  return (
    <header className="h-16 border-b border-slate-200 bg-white flex items-center justify-between px-6">
      <h1 className="text-lg font-semibold text-slate-900">{currentPageLabel(pathname)}</h1>

      <div className="flex items-center gap-4">
        <button className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2 transition">
          + Buy Number
        </button>

        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-slate-200 flex items-center justify-center text-xs font-semibold text-slate-600">
            {user?.email?.[0]?.toUpperCase() ?? "?"}
          </div>
          <div className="text-sm">
            <div className="font-medium text-slate-900">
              {user?.email ?? "Loading..."}
            </div>
            <div className="text-xs text-slate-500 capitalize">{user?.role}</div>
          </div>
          <button
            onClick={handleLogout}
            className="text-xs text-slate-500 hover:text-slate-800 ml-2"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
