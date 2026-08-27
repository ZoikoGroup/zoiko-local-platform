"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  listStaffAccounts,
  listStuckProvisioning,
  listDueForRenewal,
  listProviderStatuses,
  listIncidents,
  listKillSwitches,
  listStaffCases,
  listStaffPortingRequests,
  listFraudCases,
  listBillingActions,
  listStaffAuditEvents,
  listStaffTeam,
  getPlatformMetrics,
  getEventOutboxSummary,
  ApiError,
  type ProviderStatus,
  type Incident,
  type KillSwitch,
  type AuditEvent,
  type PlatformMetrics,
  type EventOutboxSummary,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";
import { useStaffRole, hasAnyCapability } from "@/lib/staffRole";

// One card per action queue a staff member might need to act on. Each is
// fetched independently (Promise.allSettled below) so one slow/failing
// endpoint never blocks the rest of the page from rendering - this
// backend's DB round-trips are known to be slow/occasionally flaky (Neon
// connection latency), and a dashboard that blanks out entirely because
// one of eight parallel calls timed out would be worse than one card
// quietly saying "Couldn't load" while the other seven show real numbers.
//
// `capabilities` mirrors the same keys the console nav (layout.tsx) gates
// each tab on - a role that can't act on a queue doesn't see its bar here
// either, so SUPER_ADMIN sees every queue and SUPPORT/COMPLIANCE_OFFICER
// only see the ones they can actually do something about.
type QueueRow = {
  key: string;
  label: string;
  href: string;
  count: number | null;
  error: boolean;
  capabilities?: string[];
};

// ── Icons - same hand-rolled stroke style as the console layout's nav
// icons (viewBox 24, stroke=currentColor, no icon library dependency). ──
function Icon({ path, className }: { path: React.ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ?? "h-5 w-5"}
      aria-hidden
    >
      {path}
    </svg>
  );
}
const ICONS = {
  users: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20a6 6 0 0112 0M16.5 5.5a3 3 0 010 5.8M18 20a5.5 5.5 0 00-3-4.9" />
    </>
  ),
  listChecks: (
    <>
      <path d="M4 6h2m0 0l1.5 1.5L9 6M4 12h2m0 0l1.5 1.5L9 12M4 18h2m0 0l1.5 1.5L9 18" />
      <path d="M12 6h8M12 12h8M12 18h8" />
    </>
  ),
  alert: (
    <>
      <path d="M12 3l9 16H3l9-16Z" />
      <path d="M12 9v4M12 16h.01" />
    </>
  ),
  pulse: (
    <>
      <path d="M3 12h4l2-7 4 14 2-7h6" />
    </>
  ),
  phone: (
    <>
      <path d="M4.5 4h3.2l1.5 4.2-2 1.6a11.5 11.5 0 005.6 5.6l1.6-2 4.2 1.5v3.2a1.5 1.5 0 01-1.6 1.5A16.5 16.5 0 013 5.6 1.5 1.5 0 014.5 4Z" />
    </>
  ),
  wallet: (
    <>
      <path d="M3.5 7.5a2 2 0 012-2h11a2 2 0 012 2v9a2 2 0 01-2 2h-11a2 2 0 01-2-2v-9Z" />
      <path d="M16.5 12h2.5v3h-2.5a1.5 1.5 0 010-3Z" />
    </>
  ),
};

// ── Stat tile - value/label/icon, optionally toned for status (good /
// warning / critical). Per the dataviz skill: a handful of headline
// numbers is a KPI row of stat tiles, not a chart. ──
type Tone = "default" | "good" | "warning" | "critical";
const TONE_STYLES: Record<Tone, { ring: string; icon: string }> = {
  default: { ring: "border-(--surface-border)", icon: "bg-(--surface-hover) text-(--text-body)" },
  good: { ring: "border-emerald-900/60", icon: "bg-emerald-950 text-emerald-400" },
  warning: { ring: "border-amber-900/60", icon: "bg-amber-950 text-amber-400" },
  critical: { ring: "border-red-900/60", icon: "bg-red-950 text-red-400" },
};

function StatTile({
  icon,
  label,
  value,
  subtext,
  tone = "default",
  href,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  subtext?: string;
  tone?: Tone;
  href?: string;
}) {
  const t = TONE_STYLES[tone];
  const content = (
    <div className={`bg-(--surface-card) border ${t.ring} rounded-xl p-4 flex items-start gap-3 h-full transition`}>
      <div className={`shrink-0 rounded-lg p-2 ${t.icon}`}>
        <Icon path={icon} className="h-4.5 w-4.5" />
      </div>
      <div className="min-w-0">
        <div className="text-2xl font-semibold leading-tight text-(--text-heading)">{value}</div>
        <div className="text-sm text-(--text-muted) mt-0.5 truncate">{label}</div>
        {subtext && <div className="text-xs text-(--text-muted) mt-0.5">{subtext}</div>}
      </div>
    </div>
  );
  return href ? (
    <Link href={href} className="block h-full hover:opacity-90 transition">
      {content}
    </Link>
  ) : (
    content
  );
}

// ── Horizontal bar "chart" - a magnitude comparison across named
// categories, one measure, one hue (indigo, this console's existing
// accent) per the sequential-is-the-default rule. Sorted descending so
// the largest bar is immediately visible. Bars grow from a square left
// baseline to a 4px-rounded right end; value sits at the tip, outside the
// fill, per the mark spec. Shared by the action-queue list and the new
// business-metrics breakdowns below - same component, not a re-invented
// one per section. ──
type BarRow = { key: string; label: string; count: number | null; href?: string; error?: boolean };

function BarChart({ rows }: { rows: BarRow[] }) {
  const maxCount = Math.max(1, ...rows.map((r) => r.count ?? 0));
  const sorted = [...rows].sort((a, b) => (b.count ?? 0) - (a.count ?? 0));
  return (
    <div className="space-y-3">
      {sorted.map((r) => {
        const pct = r.count === null ? 0 : Math.round((r.count / maxCount) * 100);
        const row = (
          <>
            <span className="text-sm text-(--text-body) truncate capitalize">{r.label}</span>
            <span className="h-3 rounded-full bg-(--surface-hover) overflow-hidden">
              {r.error ? (
                <span className="block h-full w-full" />
              ) : (
                <span
                  className="block h-full rounded-full bg-indigo-500 transition-all"
                  style={{ width: `${pct}%` }}
                />
              )}
            </span>
            <span className="text-sm text-(--text-muted) text-right tabular-nums">
              {r.error ? "—" : r.count === null ? "…" : r.count}
            </span>
          </>
        );
        const rowClass = "group grid grid-cols-[minmax(0,1fr)_minmax(0,2.2fr)_2.5rem] items-center gap-3";
        return r.href ? (
          <Link key={r.key} href={r.href} className={rowClass}>
            {row}
          </Link>
        ) : (
          <div key={r.key} className={rowClass}>
            {row}
          </div>
        );
      })}
    </div>
  );
}

// ── Meter - fill carries severity (more unhealthy = warmer), unfilled
// track is a lighter step of the same neutral ramp, per the meter spec. ──
function HealthMeter({ healthy, total }: { healthy: number; total: number }) {
  const pct = total === 0 ? 100 : Math.round((healthy / total) * 100);
  const fillClass = pct === 100 ? "bg-emerald-500" : pct >= 75 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-(--text-muted)">
        <span>Reachable</span>
        <span className="tabular-nums">
          {healthy} / {total}
        </span>
      </div>
      <div className="h-2.5 rounded-full bg-(--surface-hover) overflow-hidden">
        <span className={`block h-full rounded-full ${fillClass} transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function formatMinorUnits(minorUnits: number, currencyCode: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: currencyCode, maximumFractionDigits: 0 }).format(
    minorUnits / 100
  );
}

export default function StaffOverviewPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const { role, capabilities: grantedCapabilities } = useStaffRole();

  const [loading, setLoading] = useState(true);
  const [providers, setProviders] = useState<ProviderStatus[] | null>(null);
  const [providersError, setProvidersError] = useState(false);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [incidentsError, setIncidentsError] = useState(false);
  const [killSwitches, setKillSwitches] = useState<KillSwitch[] | null>(null);
  const [killSwitchesError, setKillSwitchesError] = useState(false);
  const [accountCount, setAccountCount] = useState<number | null>(null);
  // Super Admin-only "best details" widgets below - every other role has
  // no capability that touches the audit log or platform billing/call
  // volume, so this is deliberately extra depth just for the role that's
  // accountable for everything happening on the platform, not a general
  // feature every staff member gets.
  const [recentAudit, setRecentAudit] = useState<AuditEvent[] | null>(null);
  const [recentAuditError, setRecentAuditError] = useState(false);
  const [platformMetrics, setPlatformMetrics] = useState<PlatformMetrics | null>(null);
  const [platformMetricsError, setPlatformMetricsError] = useState(false);
  const [teamCount, setTeamCount] = useState<number | null>(null);
  const [eventOutbox, setEventOutbox] = useState<EventOutboxSummary | null>(null);
  const [eventOutboxError, setEventOutboxError] = useState(false);
  const [queues, setQueues] = useState<QueueRow[]>([]);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;

    return Promise.allSettled([
      listProviderStatuses(token),
      listIncidents(50),
      listKillSwitches(token),
      listStaffAccounts(token),
      listBillingActions(token, "pending"),
      listStaffCases(token, "pending"),
      listFraudCases(token, "open"),
      listStaffPortingRequests(token, "submitted"),
      listStuckProvisioning(token),
      listDueForRenewal(token),
    ]).then(
      ([
        providersResult,
        incidentsResult,
        killSwitchesResult,
        accountsResult,
        billingActionsResult,
        complianceCasesResult,
        fraudCasesResult,
        portingResult,
        stuckProvisioningResult,
        dueForRenewalResult,
      ]) => {
        // A 401 from any one of these means the token itself is dead - bail
        // out to login rather than rendering a page full of "Couldn't load"
        // cards.
        const anyUnauthorized = [providersResult, incidentsResult, killSwitchesResult, accountsResult].some(
          (r) => r.status === "rejected" && r.reason instanceof ApiError && r.reason.status === 401
        );
        if (anyUnauthorized) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }

        if (providersResult.status === "fulfilled") {
          setProviders(providersResult.value.providers);
          setProvidersError(false);
        } else {
          setProvidersError(true);
        }

        if (incidentsResult.status === "fulfilled") {
          setIncidents(incidentsResult.value.filter((i) => i.status !== "resolved"));
          setIncidentsError(false);
        } else {
          setIncidentsError(true);
        }

        if (killSwitchesResult.status === "fulfilled") {
          setKillSwitches(killSwitchesResult.value.filter((k) => k.is_active));
          setKillSwitchesError(false);
        } else {
          setKillSwitchesError(true);
        }

        if (accountsResult.status === "fulfilled") {
          setAccountCount(accountsResult.value.length);
        } else {
          setAccountCount(null);
        }

        setQueues([
          {
            key: "billing",
            label: "Pending billing approvals",
            href: "/staff/billing",
            count: billingActionsResult.status === "fulfilled" ? billingActionsResult.value.length : null,
            error: billingActionsResult.status === "rejected",
            capabilities: ["billing.approve_billing_action"],
          },
          {
            key: "compliance",
            label: "Open compliance cases",
            href: "/staff/cases",
            count: complianceCasesResult.status === "fulfilled" ? complianceCasesResult.value.length : null,
            error: complianceCasesResult.status === "rejected",
            capabilities: ["compliance.review_case"],
          },
          {
            key: "fraud",
            label: "Open fraud cases",
            href: "/staff/fraud",
            count: fraudCasesResult.status === "fulfilled" ? fraudCasesResult.value.length : null,
            error: fraudCasesResult.status === "rejected",
            capabilities: ["risk.resolve_fraud_case"],
          },
          {
            key: "porting",
            label: "Porting requests to review",
            href: "/staff/porting",
            count: portingResult.status === "fulfilled" ? portingResult.value.length : null,
            error: portingResult.status === "rejected",
            capabilities: ["porting.review_request"],
          },
          {
            key: "provisioning",
            label: "Numbers stuck provisioning",
            href: "/staff/provisioning",
            count: stuckProvisioningResult.status === "fulfilled" ? stuckProvisioningResult.value.length : null,
            error: stuckProvisioningResult.status === "rejected",
            capabilities: ["numbers.manage_provisioning"],
          },
          {
            key: "renewals",
            label: "Numbers due for renewal",
            href: "/staff/provisioning",
            count: dueForRenewalResult.status === "fulfilled" ? dueForRenewalResult.value.length : null,
            error: dueForRenewalResult.status === "rejected",
            capabilities: ["numbers.manage_renewal"],
          },
        ]);

        setLoading(false);
      }
    );
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!token || role !== "super_admin") return;
    listStaffAuditEvents(token)
      .then((events) => {
        setRecentAudit(events.slice(0, 8));
        setRecentAuditError(false);
      })
      .catch(() => setRecentAuditError(true));
    getPlatformMetrics(token)
      .then((metrics) => {
        setPlatformMetrics(metrics);
        setPlatformMetricsError(false);
      })
      .catch(() => setPlatformMetricsError(true));
    listStaffTeam(token)
      .then((team) => setTeamCount(team.length))
      .catch(() => setTeamCount(null));
    getEventOutboxSummary(token)
      .then((summary) => {
        setEventOutbox(summary);
        setEventOutboxError(false);
      })
      .catch(() => setEventOutboxError(true));
  }, [token, role]);

  if (!token) return null;

  const activeKillSwitches = killSwitches ?? [];
  const openIncidents = incidents ?? [];
  const configuredProviders = (providers ?? []).filter((p) => p.configured);
  const downProviders = configuredProviders.filter((p) => !p.ok);
  const visibleQueues = queues.filter((q) => hasAnyCapability(grantedCapabilities, q.capabilities));
  const totalActionable = visibleQueues.reduce((sum, q) => sum + (q.count ?? 0), 0);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-(--text-heading)">Overview</h2>
        <p className="text-sm text-(--text-muted)">
          What&apos;s happening across the platform right now — everything below links straight
          into its own tab.
        </p>
      </div>

      {loading && queues.length === 0 && <p className="text-sm text-(--text-muted)">Loading...</p>}

      {/* ── Kill switches — loudest thing on the page when active ── */}
      {activeKillSwitches.length > 0 && (
        <div className="rounded-xl border border-red-900 bg-red-950/60 px-5 py-4">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-red-400 animate-pulse" />
            <h3 className="font-semibold text-red-200">
              {activeKillSwitches.length} kill switch{activeKillSwitches.length > 1 ? "es" : ""} active
            </h3>
          </div>
          <ul className="mt-2 space-y-1 text-sm text-red-300/90">
            {activeKillSwitches.map((k) => (
              <li key={k.id}>
                <span className="font-mono">{k.scope}</span>
                {k.reason ? ` — ${k.reason}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {killSwitchesError && (
        <p className="text-xs text-amber-400">Couldn&apos;t load kill-switch status.</p>
      )}

      {/* ── KPI row ── */}
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        <StatTile
          icon={ICONS.users}
          label="Total accounts"
          value={accountCount === null ? "…" : String(accountCount)}
          href="/staff/accounts"
        />
        <StatTile
          icon={ICONS.listChecks}
          label="Items waiting on a decision"
          value={visibleQueues.length === 0 ? "—" : String(totalActionable)}
          tone={totalActionable > 0 ? "warning" : "good"}
        />
        <StatTile
          icon={ICONS.alert}
          label="Open incidents"
          value={incidents === null ? "…" : String(openIncidents.length)}
          tone={openIncidents.length > 0 ? "critical" : "good"}
          href="/staff/incidents"
        />
        <StatTile
          icon={ICONS.pulse}
          label="Providers reachable"
          value={providers === null ? "…" : `${configuredProviders.length - downProviders.length}/${configuredProviders.length}`}
          tone={providers === null ? "default" : downProviders.length > 0 ? "critical" : "good"}
          href="/staff/providers"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ── Provider health ── */}
        <div className="bg-(--surface-card) border border-(--surface-border) rounded-xl p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-(--text-heading)">Provider health</h3>
            <Link href="/staff/providers" className="text-xs text-indigo-400 hover:text-indigo-300">
              View all
            </Link>
          </div>
          {providersError && <p className="text-sm text-red-400">Couldn&apos;t load provider status.</p>}
          {!providersError && providers === null && <p className="text-sm text-(--text-muted)">Loading...</p>}
          {providers !== null && configuredProviders.length > 0 && (
            <>
              <HealthMeter healthy={configuredProviders.length - downProviders.length} total={configuredProviders.length} />
              {downProviders.length > 0 && (
                <ul className="space-y-1.5 pt-1">
                  {downProviders.map((p) => (
                    <li key={p.name} className="flex items-center gap-2 text-sm">
                      <span className="h-1.5 w-1.5 rounded-full bg-red-400 shrink-0" />
                      <span className="text-(--text-body)">{p.name}</span>
                      {p.detail && <span className="text-xs text-(--text-muted) truncate">{p.detail}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>

        {/* ── Open incidents ── */}
        <div className="bg-(--surface-card) border border-(--surface-border) rounded-xl p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-(--text-heading)">Open incidents</h3>
            <Link href="/staff/incidents" className="text-xs text-indigo-400 hover:text-indigo-300">
              View all
            </Link>
          </div>
          {incidentsError && <p className="text-sm text-red-400">Couldn&apos;t load incidents.</p>}
          {!incidentsError && incidents === null && <p className="text-sm text-(--text-muted)">Loading...</p>}
          {incidents !== null && (
            <>
              {openIncidents.length === 0 ? (
                <p className="text-sm text-emerald-400">No open incidents.</p>
              ) : (
                <ul className="space-y-1.5">
                  {openIncidents.slice(0, 5).map((i) => (
                    <li key={i.id} className="text-sm text-(--text-body)">
                      <span className="text-(--text-muted) capitalize">[{i.status}]</span> {i.title}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── Action queues — horizontal bar chart, sorted by most backed-up
          first: one measure (items waiting) across named categories is a
          magnitude comparison, so length + one hue carries it rather than
          a grid of same-size boxes that give every queue equal visual
          weight regardless of how backed up it actually is. ── */}
      {visibleQueues.length > 0 && (
        <div className="bg-(--surface-card) border border-(--surface-border) rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-medium text-(--text-heading)">Waiting on a decision</h3>
            {totalActionable > 0 && (
              <span className="text-xs text-(--text-muted)">{totalActionable} total across every queue</span>
            )}
          </div>
          <BarChart rows={visibleQueues} />
        </div>
      )}

      {/* ── Super Admin-only: platform-wide call volume + subscription/
          revenue snapshot. No other role has a capability that touches
          billing or cross-account call records, so this - like the
          recent-activity feed below - is depth specific to being
          accountable for the whole business, not a general feature. ── */}
      {role === "super_admin" && (
        <div className="space-y-4">
          <h3 className="font-medium text-(--text-heading)">Business overview — Super Admin only</h3>
          {platformMetricsError && (
            <p className="text-sm text-red-400">Couldn&apos;t load platform call/billing metrics.</p>
          )}
          {!platformMetricsError && platformMetrics === null && (
            <p className="text-sm text-(--text-muted)">Loading...</p>
          )}
          {platformMetrics && (
            <>
              <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
                <StatTile
                  icon={ICONS.phone}
                  label={`Calls (last ${platformMetrics.calls.window_days}d)`}
                  value={String(platformMetrics.calls.total_calls)}
                  href="/staff/providers"
                />
                <StatTile
                  icon={ICONS.pulse}
                  label="Call minutes"
                  value={platformMetrics.calls.total_minutes.toLocaleString()}
                />
                <StatTile
                  icon={ICONS.users}
                  label="Active subscriptions"
                  value={String(platformMetrics.billing.total_active_subscriptions)}
                  href="/staff/billing"
                />
                <StatTile
                  icon={ICONS.wallet}
                  label="Est. MRR"
                  value={formatMinorUnits(platformMetrics.billing.estimated_mrr_minor_units, platformMetrics.billing.currency_code)}
                  subtext="Planning estimate — see Billing tab"
                  href="/staff/billing"
                />
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <div className="bg-(--surface-card) border border-(--surface-border) rounded-xl p-5">
                  <h4 className="font-medium text-(--text-heading) mb-4">
                    Calls by status <span className="text-(--text-muted) font-normal">— last {platformMetrics.calls.window_days}d</span>
                  </h4>
                  {platformMetrics.calls.by_status.length === 0 ? (
                    <p className="text-sm text-(--text-muted)">No calls in this window yet.</p>
                  ) : (
                    <BarChart
                      rows={platformMetrics.calls.by_status.map((s) => ({
                        key: s.status,
                        label: s.status.replace(/-/g, " "),
                        count: s.count,
                      }))}
                    />
                  )}
                </div>
                <div className="bg-(--surface-card) border border-(--surface-border) rounded-xl p-5">
                  <h4 className="font-medium text-(--text-heading) mb-4">Active subscriptions by plan</h4>
                  {platformMetrics.billing.by_plan.length === 0 ? (
                    <p className="text-sm text-(--text-muted)">No active subscriptions yet.</p>
                  ) : (
                    <BarChart
                      rows={platformMetrics.billing.by_plan.map((p) => ({
                        key: p.plan_code,
                        label: p.plan_name,
                        count: p.count,
                      }))}
                    />
                  )}
                </div>
              </div>
            </>
          )}

          <div className="grid gap-3 grid-cols-2">
            <StatTile
              icon={ICONS.users}
              label="Staff team members"
              value={teamCount === null ? "…" : String(teamCount)}
              href="/staff/team"
            />
            <StatTile
              icon={ICONS.listChecks}
              label="Event outbox pending"
              value={eventOutboxError ? "—" : eventOutbox === null ? "…" : String(eventOutbox.pending_count)}
              tone={eventOutbox && eventOutbox.failing_count > 0 ? "critical" : eventOutbox && eventOutbox.pending_count > 0 ? "warning" : "good"}
              subtext={eventOutbox && eventOutbox.failing_count > 0 ? `${eventOutbox.failing_count} retried 3+ times` : undefined}
              href="/staff/incidents"
            />
          </div>
        </div>
      )}

      {/* ── Super Admin-only: full recent activity across the platform.
          Every other role's own action already shows up in the Audit Log
          tab on request - this is the extra, unprompted depth that comes
          with being accountable for everything happening, not just the
          areas a given role can act in. ── */}
      {role === "super_admin" && (
        <div className="bg-(--surface-card) border border-indigo-900/60 rounded-xl p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-medium text-(--text-heading)">Recent platform activity</h3>
              <p className="text-xs text-(--text-muted) mt-0.5">
                Every state-changing action across every account and every staff member — Super Admin only.
              </p>
            </div>
            <Link href="/staff/audit" className="text-xs text-indigo-400 hover:text-indigo-300 shrink-0">
              Full log
            </Link>
          </div>
          {recentAuditError && <p className="text-sm text-red-400">Couldn&apos;t load recent activity.</p>}
          {!recentAuditError && recentAudit === null && <p className="text-sm text-(--text-muted)">Loading...</p>}
          {recentAudit !== null && (
            <>
              {recentAudit.length === 0 ? (
                <p className="text-sm text-(--text-muted)">No activity recorded yet.</p>
              ) : (
                <div className="divide-y divide-(--surface-border)">
                  {recentAudit.map((e) => (
                    <div key={e.id} className="flex items-center justify-between gap-4 py-2 text-sm">
                      <div className="min-w-0">
                        <span className="font-mono text-(--text-body)">{e.action}</span>
                        <span className="text-(--text-muted)"> on </span>
                        <span className="font-mono text-(--text-muted) truncate">{e.target}</span>
                      </div>
                      <div className="flex shrink-0 items-center gap-3 text-xs text-(--text-muted)">
                        <span className="font-mono">{e.actor}</span>
                        <span>{new Date(e.created_at).toLocaleString()}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
