"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearStaffToken } from "@/lib/staffAuth";

const NAV_ITEMS = [
  { href: "/staff/cases", label: "Compliance Cases" },
  { href: "/staff/porting", label: "Number Porting" },
  { href: "/staff/accounts", label: "Accounts" },
  { href: "/staff/audit", label: "Audit Log" },
  { href: "/staff/providers", label: "Provider Status" },
];

export default function StaffConsoleLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  function handleLogout() {
    clearStaffToken();
    router.push("/staff/login");
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200">
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between">
        <div>
          <div className="font-semibold text-white">Zoiko Local — Ops Console</div>
          <div className="text-xs text-slate-500">Internal staff only</div>
        </div>
        <button onClick={handleLogout} className="text-xs text-slate-400 hover:text-white transition">
          Log out
        </button>
      </header>

      <nav className="border-b border-slate-800 px-6">
        <div className="max-w-4xl mx-auto flex gap-1">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`px-3 py-3 text-sm border-b-2 transition ${
                pathname === item.href
                  ? "border-slate-200 text-white"
                  : "border-transparent text-slate-500 hover:text-slate-300"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </div>
      </nav>

      <main className="max-w-4xl mx-auto p-6 space-y-4">{children}</main>
    </div>
  );
}
