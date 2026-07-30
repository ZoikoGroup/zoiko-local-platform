"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/dashboard" },
  { label: "Phone Numbers", href: "/dashboard/numbers" },
  { label: "Calls", href: "/dashboard/calls" },
  { label: "Video", href: "/dashboard/video" },
  { label: "AI Insights", href: "/dashboard/ai-insights" },
  { label: "Contacts", href: "/dashboard/contacts" },
  { label: "Billing & Usage", href: "/dashboard/billing" },
  { label: "Integrations", href: "/dashboard/integrations" },
  { label: "Reports", href: "/dashboard/reports" },
  { label: "Settings", href: "/dashboard/settings" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 shrink-0 bg-slate-950 text-slate-300 flex flex-col">
      <div className="flex items-center gap-2 px-5 py-5 border-b border-white/10">
        <div className="w-9 h-9 rounded-lg bg-indigo-600 text-white flex items-center justify-center font-bold">
          Z
        </div>
        <div>
          <div className="font-semibold text-white leading-tight">Zoiko Local</div>
          <div className="text-xs text-slate-400 leading-tight">Communications Platform</div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/dashboard"
              ? pathname === "/dashboard"
              : pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`block rounded-lg px-3 py-2 text-sm font-medium transition ${
                active
                  ? "bg-indigo-600 text-white"
                  : "text-slate-300 hover:bg-white/5 hover:text-white"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
