"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS } from "@/lib/nav";
import { NAV_ICONS } from "@/components/NavIcons";
import Logo from "@/components/Logo";
import type { Subscription } from "@/lib/api";

// Home and Billing stay open for a trial account no matter what - Billing
// is literally the upgrade path, and Home's own stat cards need to keep
// working (see app.core.deps.require_paid_or_read_only's docstring for
// the matching backend-side allowlist logic this mirrors on the nav).
const ALWAYS_OPEN_HREFS = new Set(["/dashboard", "/dashboard/billing"]);

function LockIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V7a4 4 0 018 0v4" />
    </svg>
  );
}

export default function Sidebar({ subscription }: { subscription: Subscription | null }) {
  const pathname = usePathname();
  const isTrialing = subscription?.status === "trialing";

  return (
    <aside className="w-64 shrink-0 bg-slate-950 text-slate-300 flex flex-col">
      <div className="flex items-center gap-2 px-5 py-5 border-b border-white/10">
        <Logo size={36} className="shadow-sm shadow-indigo-500/30" />
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
          const locked = isTrialing && !ALWAYS_OPEN_HREFS.has(item.href);

          if (locked) {
            return (
              <Link
                key={item.href}
                href="/dashboard/billing"
                title={`Upgrade to unlock ${item.label}`}
                className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-white/5 hover:text-slate-400 transition"
              >
                {Icon && <Icon className="w-[18px] h-[18px] shrink-0 text-slate-700" />}
                <span className="flex-1">{item.label}</span>
                <LockIcon className="w-3.5 h-3.5 shrink-0 text-slate-600" />
              </Link>
            );
          }

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
