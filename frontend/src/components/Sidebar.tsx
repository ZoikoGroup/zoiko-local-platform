"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS } from "@/lib/nav";
import { NAV_ICONS } from "@/components/NavIcons";

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 shrink-0 bg-slate-950 text-slate-300 flex flex-col">
      <div className="flex items-center gap-2 px-5 py-5 border-b border-white/10">
        <img src="/logo-icon.svg" alt="Zoiko Local" className="w-9 h-9 rounded-lg shadow-sm shadow-indigo-500/30" />
        <div>
          <div className="font-semibold text-white leading-tight">Zoiko Local</div>
          <div className="text-xs text-slate-400 leading-tight">Communications Platform</div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/dashboard"
              ? pathname === "/dashboard"
              : pathname?.startsWith(item.href);
          const Icon = NAV_ICONS[item.href];
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${
                active
                  ? "bg-white/10 text-white"
                  : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
              }`}
            >
              {active && (
                <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-indigo-500" />
              )}
              {Icon && <Icon className={`w-[18px] h-[18px] shrink-0 ${active ? "text-indigo-400" : "text-slate-500"}`} />}
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
