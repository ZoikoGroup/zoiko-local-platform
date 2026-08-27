"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearStaffToken } from "@/lib/staffAuth";
import { StaffRoleProvider, useStaffRole, hasAnyCapability } from "@/lib/staffRole";
import { useStaffTheme } from "@/lib/staffTheme";
import "../staff-theme.css";

// Hrefs and labels are unchanged from the original NAV_ITEMS array — only
// grouped and given icons. Ten flat items in a horizontal tab strip inside
// a max-w-4xl container meant the last few tabs overflowed and the active
// one was hard to spot; grouping makes the console navigable.
//
// `capabilities`: which of this tab's actions actually matter for deciding
// whether to show it at all - undefined/empty means "no gated action lives
// here, show to every role" (Overview, Accounts, Provider Status, Audit
// Log are all diagnostic/lookup pages any staff role can use). Where a tab
// DOES have gated actions, it's shown only to a role that can perform at
// least one of them - deliberately NOT re-deriving this from a hardcoded
// per-role list (that would be a second copy of the real access-control
// decision, which already lives in StaffCapabilityGrant/access-matrix, and
// could drift from it). See isVisibleForRole below, which cross-checks
// these keys against the real GET /staff/access-matrix response.
type NavItem = { href: string; label: string; icon: IconName; capabilities?: string[] };

const NAV_GROUPS: { group: string; items: NavItem[] }[] = [
  {
    group: "",
    items: [{ href: "/staff", label: "Overview", icon: "home" }],
  },
  {
    group: "Operations",
    items: [
      { href: "/staff/cases", label: "Compliance Cases", icon: "clipboard", capabilities: ["compliance.review_case"] },
      { href: "/staff/porting", label: "Number Porting", icon: "transfer", capabilities: ["porting.review_request"] },
      {
        href: "/staff/provisioning",
        label: "Provisioning Recovery",
        icon: "wrench",
        capabilities: ["numbers.manage_provisioning"],
      },
    ],
  },
  {
    group: "Risk",
    items: [
      {
        href: "/staff/fraud",
        label: "Risk & Fraud",
        icon: "shield",
        capabilities: [
          "risk.manage_blocked_destinations",
          "risk.manage_fraud_rules",
          "risk.resolve_fraud_case",
          "risk.manage_account_risk_state",
          "risk.reinstate_account",
        ],
      },
      { href: "/staff/accounts", label: "Accounts", icon: "users" },
    ],
  },
  {
    group: "Platform",
    items: [
      { href: "/staff/providers", label: "Provider Status", icon: "plug" },
      { href: "/staff/incidents", label: "Incidents & Errors", icon: "alert", capabilities: ["ops.manage_incidents"] },
      {
        href: "/staff/billing",
        label: "Billing (ZoikoNex)",
        icon: "receipt",
        capabilities: [
          "billing.manage_price_catalog",
          "billing.simulate_payment_event",
          "billing.resolve_reconciliation_exception",
          "billing.approve_billing_action",
          "billing.run_billing_cycle",
          "billing.issue_credit_note",
          "billing.issue_debit_note",
          "billing.refund_payment",
          "billing.terminate_subscription",
          "billing.manage_calling_rates",
          "billing.manage_number_rates",
          "billing.manage_ai_usage_rates",
        ],
      },
    ],
  },
  {
    group: "Governance",
    items: [
      { href: "/staff/audit", label: "Audit Log", icon: "history" },
      {
        href: "/staff/access-matrix",
        label: "Access Matrix",
        icon: "grid",
        capabilities: ["staff.manage_capabilities"],
      },
      {
        href: "/staff/team",
        label: "Team",
        icon: "userPlus",
        capabilities: ["staff.manage_staff_accounts"],
      },
    ],
  },
];

const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

type IconName =
  | "home"
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
  | "sun"
  | "moon"
  | "userPlus";

const ICON_PATHS: Record<IconName, React.ReactNode> = {
  home: (
    <>
      <path d="M4 11.5 12 4l8 7.5" />
      <path d="M6 10v9a1 1 0 001 1h4v-6h2v6h4a1 1 0 001-1v-9" />
    </>
  ),
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
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2.5M12 19.5V22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M2 12h2.5M19.5 12H22M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" />
    </>
  ),
  moon: <path d="M20.5 14.5A8.5 8.5 0 119.5 3.5a7 7 0 0011 11Z" />,
  userPlus: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20a6 6 0 0112 0" />
      <path d="M18 8v6M15 11h6" />
    </>
  ),
};

// "/staff" (Overview) must only match itself - a plain startsWith would
// also mark it active on every other route ("/staff/accounts" etc. all
// start with "/staff/"), highlighting two nav items at once.
function isActiveHref(href: string, pathname: string | null): boolean {
  if (pathname === null) return false;
  if (href === "/staff") return pathname === "/staff";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ?? "h-[17px] w-[17px] shrink-0"}
      aria-hidden
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

const ROLE_LABELS: Record<string, string> = {
  super_admin: "Super Admin",
  compliance_officer: "Compliance Officer",
  support: "Support",
};

function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { role, capabilities, loading: roleLoading } = useStaffRole();
  const [theme, setTheme] = useStaffTheme();

  function handleLogout() {
    clearStaffToken();
    router.push("/staff/login");
  }

  // While the role is still loading, show nothing gated rather than
  // everything - avoids a flash of SUPER_ADMIN-only tabs for an account
  // that turns out to be SUPPORT once the real role lands a moment later.
  const visibleGroups = NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) =>
      roleLoading ? !item.capabilities?.length : hasAnyCapability(capabilities, item.capabilities)
    ),
  })).filter((group) => group.items.length > 0);
  const visibleItems = visibleGroups.flatMap((g) => g.items);

  const current = ALL_ITEMS.find((i) => isActiveHref(i.href, pathname));

  // SCROLL OWNERSHIP — h-screen + overflow-hidden, not min-h-screen.
  // A child's overflow-y-auto only scrolls when its height is constrained.
  // With min-h-screen the shell grows with content, the window scrolls, and
  // the sidebar slides away with it. Pinning the shell to the viewport gives
  // the nav and the content independent scrollbars. The min-h-0 further down
  // is also required: flex-1 leaves min-height:auto, which refuses to shrink
  // below content height, so overflow-y-auto would never engage without it.
  return (
    <div
      className={`staff-scope${theme === "light" ? " light" : ""} h-screen flex overflow-hidden bg-(--surface-page) text-(--text-body)`}
    >
      {/* ── SIDEBAR ────────────────────────────────────────────────── */}
      <aside className="hidden w-64 shrink-0 h-full flex-col border-r border-(--surface-border) bg-(--surface-card) lg:flex">
        <div className="shrink-0 border-b border-(--surface-border) px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">
              Z
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold text-(--text-heading)">Ops Console</div>
              <div className="text-[11px] text-slate-400">Internal staff only</div>
            </div>
          </div>
        </div>

        <nav className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-3 py-4">
          {visibleGroups.map((group) => (
            <div key={group.group || "_root"} className="mb-5 last:mb-0">
              {group.group && (
                <div className="px-3 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-(--text-muted)">
                  {group.group}
                </div>
              )}
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const active = isActiveHref(item.href, pathname);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={`relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${
                        active
                          ? "bg-(--nav-active-bg) font-medium text-(--nav-active-text)"
                          : "text-(--text-muted) hover:bg-(--surface-hover) hover:text-(--text-body)"
                      }`}
                    >
                      {active && (
                        <span className="absolute left-0 top-1.5 bottom-1.5 w-[2.5px] rounded-full bg-indigo-400" />
                      )}
                      <span className={active ? "text-indigo-400" : "text-(--text-muted)"}>
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

        <div className="shrink-0 border-t border-(--surface-border) px-5 py-3.5">
          <div className="flex items-center gap-2 font-mono text-[11px] text-(--text-muted)">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            staff-api connected
          </div>
        </div>
      </aside>

      {/* ── MAIN COLUMN ────────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
        <header className="shrink-0 flex items-center justify-between gap-4 border-b border-(--surface-border) bg-(--surface-card) px-6 py-3.5">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold text-(--text-heading)">
              {current?.label ?? "Ops Console"}
            </h1>
            <div className="text-[11px] text-(--text-muted)">Zoiko Local — internal operations</div>
          </div>

          <div className="flex shrink-0 items-center gap-3">
            {role && (
              <span
                className={`hidden rounded-full border px-2.5 py-1 font-mono text-[10.5px] sm:inline ${
                  role === "super_admin"
                    ? "border-indigo-800 bg-indigo-950 text-indigo-300"
                    : "border-(--surface-border) bg-(--surface-hover) text-(--text-body)"
                }`}
              >
                {ROLE_LABELS[role] ?? role}
              </span>
            )}
            <span className="hidden rounded-full border border-amber-900 bg-amber-950 px-2.5 py-1 font-mono text-[10.5px] text-amber-400 sm:inline">
              actions audited
            </span>
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              className="rounded-lg border border-(--surface-border) p-1.5 text-(--text-muted) transition hover:border-(--surface-hover) hover:bg-(--surface-hover) hover:text-(--text-heading)"
            >
              <Icon name={theme === "dark" ? "sun" : "moon"} className="h-4 w-4" />
            </button>
            <button
              onClick={handleLogout}
              className="rounded-lg border border-(--surface-border) px-3 py-1.5 text-xs font-medium text-(--text-body) transition hover:bg-(--surface-hover) hover:text-(--text-heading)"
            >
              Log out
            </button>
          </div>
        </header>

        {/* Horizontal nav retained for narrow screens, since the sidebar is
            hidden below lg. Same hrefs, no duplication of logic. */}
        <nav className="shrink-0 overflow-x-auto border-b border-(--surface-border) bg-(--surface-card) px-4 lg:hidden">
          <div className="flex gap-1">
            {visibleItems.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`whitespace-nowrap border-b-2 px-3 py-3 text-sm transition ${
                    active
                      ? "border-indigo-400 text-(--text-heading)"
                      : "border-transparent text-(--text-muted) hover:text-(--text-body)"
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

export default function StaffConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <StaffRoleProvider>
      <ConsoleShell>{children}</ConsoleShell>
    </StaffRoleProvider>
  );
}
