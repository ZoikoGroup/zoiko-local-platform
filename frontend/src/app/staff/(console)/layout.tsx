"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearStaffToken } from "@/lib/staffAuth";
import "../staff-theme.css";

// Hrefs and labels are unchanged from the original NAV_ITEMS array — only
// grouped and given icons. Ten flat items in a horizontal tab strip inside
// a max-w-4xl container meant the last few tabs overflowed and the active
// one was hard to spot; grouping makes the console navigable.
type NavItem = { href: string; label: string; icon: IconName };

const NAV_GROUPS: { group: string; items: NavItem[] }[] = [
  {
    group: "Operations",
    items: [
      { href: "/staff/cases", label: "Compliance Cases", icon: "clipboard" },
      { href: "/staff/porting", label: "Number Porting", icon: "transfer" },
      { href: "/staff/provisioning", label: "Provisioning Recovery", icon: "wrench" },
    ],
  },
  {
    group: "Risk",
    items: [
      { href: "/staff/fraud", label: "Risk & Fraud", icon: "shield" },
      { href: "/staff/accounts", label: "Accounts", icon: "users" },
    ],
  },
  {
    group: "Platform",
    items: [
      { href: "/staff/providers", label: "Provider Status", icon: "plug" },
      { href: "/staff/incidents", label: "Incidents & Errors", icon: "alert" },
      { href: "/staff/billing", label: "Billing (ZoikoNex)", icon: "receipt" },
      { href: "/staff/kill-switches", label: "Kill Switches", icon: "power" },
    ],
  },
  {
    group: "Governance",
    items: [
      { href: "/staff/audit", label: "Audit Log", icon: "history" },
      { href: "/staff/access-matrix", label: "Access Matrix", icon: "grid" },
      { href: "/staff/team", label: "Staff & Team", icon: "badge" },
    ],
  },
];

const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

type IconName =
  | "clipboard"
  | "transfer"
  | "wrench"
  | "shield"
  | "users"
  | "plug"
  | "alert"
  | "receipt"
  | "history"
  | "grid"
  | "power"
  | "badge";

const ICON_PATHS: Record<IconName, React.ReactNode> = {
  clipboard: (
    <>
      <rect x="7" y="4" width="10" height="4" rx="1.4" />
      <path d="M9 4H6.5A1.5 1.5 0 005 5.5v13A1.5 1.5 0 006.5 20h11a1.5 1.5 0 001.5-1.5v-13A1.5 1.5 0 0017.5 4H15" />
      <path d="M8.5 12h7M8.5 16h4" />
    </>
  ),
  transfer: (
    <>
      <path d="M4 8h13l-3-3M20 16H7l3 3" />
    </>
  ),
  wrench: <path d="M14.7 6.3a4 4 0 105.3 5.3L21 21H3l7.4-1a4 4 0 014.3-13.7Z" />,
  shield: (
    <>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20a6 6 0 0112 0M16.5 5.5a3 3 0 010 5.8M18 20a5.5 5.5 0 00-3-4.9" />
    </>
  ),
  plug: (
    <>
      <path d="M9 3v5M15 3v5" />
      <path d="M6 8h12v3a6 6 0 01-12 0V8ZM12 17v4" />
    </>
  ),
  alert: (
    <>
      <path d="M12 3l9 16H3l9-16Z" />
      <path d="M12 9v4M12 16h.01" />
    </>
  ),
  receipt: (
    <>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9.5 8h5M9.5 12h5" />
    </>
  ),
  history: (
    <>
      <path d="M3.5 12a8.5 8.5 0 108.5-8.5A8.4 8.4 0 006 6.5" />
      <path d="M3 3v4h4M12 8v4.5l3 1.8" />
    </>
  ),
  grid: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.4" />
      <rect x="13" y="4" width="7" height="7" rx="1.4" />
      <rect x="4" y="13" width="7" height="7" rx="1.4" />
      <rect x="13" y="13" width="7" height="7" rx="1.4" />
    </>
  ),
  power: (
    <>
      <path d="M12 3v8" />
      <path d="M7 5.5a8 8 0 105-2.5" />
    </>
  ),
  badge: (
    <>
      <circle cx="12" cy="8" r="3.2" />
      <path d="M6 20a6 6 0 0112 0" />
      <path d="M9.5 12.5 12 20l1-2.3L15 20l1.5-3.2" />
    </>
  ),
};

function Icon({ name }: { name: IconName }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-[17px] w-[17px] shrink-0"
      aria-hidden
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

export default function StaffConsoleLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  function handleLogout() {
    clearStaffToken();
    router.push("/staff/login");
  }

  const current = ALL_ITEMS.find((i) => pathname === i.href || pathname?.startsWith(`${i.href}/`));

  // SCROLL OWNERSHIP — h-screen + overflow-hidden, not min-h-screen.
  // A child's overflow-y-auto only scrolls when its height is constrained.
  // With min-h-screen the shell grows with content, the window scrolls, and
  // the sidebar slides away with it. Pinning the shell to the viewport gives
  // the nav and the content independent scrollbars. The min-h-0 further down
  // is also required: flex-1 leaves min-height:auto, which refuses to shrink
  // below content height, so overflow-y-auto would never engage without it.
  return (
    <div className="staff-scope h-screen flex overflow-hidden bg-slate-950 text-slate-200">
      {/* ── SIDEBAR ────────────────────────────────────────────────── */}
      <aside className="hidden w-64 shrink-0 h-full flex-col border-r border-slate-800 bg-slate-900 lg:flex">
        <div className="shrink-0 border-b border-slate-800 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
              Z
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold text-white">Ops Console</div>
              <div className="text-[11px] text-slate-400">Internal staff only</div>
            </div>
          </div>
        </div>

        <nav className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-3 py-4">
          {NAV_GROUPS.map((group) => (
            <div key={group.group} className="mb-5 last:mb-0">
              <div className="px-3 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                {group.group}
              </div>
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const active = pathname === item.href || pathname?.startsWith(`${item.href}/`);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={`relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${
                        active
                          ? "bg-slate-700 font-medium text-white"
                          : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                      }`}
                    >
                      {active && (
                        <span className="absolute left-0 top-1.5 bottom-1.5 w-[2.5px] rounded-full bg-indigo-400" />
                      )}
                      <span className={active ? "text-indigo-400" : "text-slate-500"}>
                        <Icon name={item.icon} />
                      </span>
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="shrink-0 border-t border-slate-800 px-5 py-3.5">
          <div className="flex items-center gap-2 font-mono text-[11px] text-slate-500">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            staff-api connected
          </div>
        </div>
      </aside>

      {/* ── MAIN COLUMN ────────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
        <header className="shrink-0 flex items-center justify-between gap-4 border-b border-slate-800 bg-slate-900 px-6 py-3.5">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold text-white">
              {current?.label ?? "Ops Console"}
            </h1>
            <div className="text-[11px] text-slate-400">Zoiko Local — internal operations</div>
          </div>

          <div className="flex shrink-0 items-center gap-3">
            <span className="hidden rounded-full border border-amber-900 bg-amber-950 px-2.5 py-1 font-mono text-[10.5px] text-amber-400 sm:inline">
              actions audited
            </span>
            <button
              onClick={handleLogout}
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-slate-600 hover:bg-slate-800 hover:text-white"
            >
              Log out
            </button>
          </div>
        </header>

        {/* Horizontal nav retained for narrow screens, since the sidebar is
            hidden below lg. Same hrefs, no duplication of logic. */}
        <nav className="shrink-0 overflow-x-auto border-b border-slate-800 bg-slate-900 px-4 lg:hidden">
          <div className="flex gap-1">
            {ALL_ITEMS.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`whitespace-nowrap border-b-2 px-3 py-3 text-sm transition ${
                    active
                      ? "border-indigo-400 text-white"
                      : "border-transparent text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        </nav>

        <main className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-6">
          {/* Widened from max-w-4xl — the console's tables and case cards
              were being squeezed into a reading-width column. */}
          <div className="mx-auto max-w-6xl space-y-4">{children}</div>
        </main>
      </div>
    </div>
  );
}
