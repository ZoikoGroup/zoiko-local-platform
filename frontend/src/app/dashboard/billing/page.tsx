"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getCurrentUser,
  listUsage,
  listPlans,
  getSubscription,
  changeSubscriptionPlan,
  getUsageSummary,
  ApiError,
  type UsageEvent,
  type Plan,
  type Subscription,
  type UsageSummary,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

function formatQuantity(event: UsageEvent): string {
  if (event.unit === "seconds") {
    const minutes = Math.floor(event.quantity / 60);
    const seconds = Math.round(event.quantity % 60);
    return `${minutes}m ${seconds}s`;
  }
  return `${event.quantity} ${event.unit}`;
}

const RESOURCE_LABELS: Record<string, string> = {
  voice_minutes: "Voice minutes",
  video_minutes: "Video minutes",
  ai_summaries: "AI summaries",
};

function UsageBar({ resource }: { resource: { resource: string; used: number; limit: number } }) {
  const pct = resource.limit > 0 ? Math.min(100, Math.round((resource.used / resource.limit) * 100)) : 0;
  const nearLimit = pct >= 90;
  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-1">
        <span className="text-slate-700">{RESOURCE_LABELS[resource.resource] ?? resource.resource}</span>
        <span className="text-slate-500">
          {resource.used.toLocaleString()} of {resource.limit.toLocaleString()}
        </span>
      </div>
      <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full ${nearLimit ? "bg-amber-500" : "bg-indigo-500"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function BillingPage() {
  const [token] = useState<string | null>(() => getToken());
  const [isAdmin, setIsAdmin] = useState(false);

  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [usageSummary, setUsageSummary] = useState<UsageSummary | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [changingPlan, setChangingPlan] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);

  const [events, setEvents] = useState<UsageEvent[]>([]);
  const [logLoading, setLogLoading] = useState(true);
  const [logError, setLogError] = useState<string | null>(null);

  const loadPlanAndUsage = useCallback(() => {
    if (!token) return;
    return Promise.all([getSubscription(token), getUsageSummary(token), listPlans(token)])
      .then(([sub, summary, planList]) => {
        setSubscription(sub);
        setUsageSummary(summary);
        setPlans(planList);
        setSummaryError(null);
      })
      .catch(() => setSummaryError("Couldn't load your plan and usage."))
      .finally(() => setSummaryLoading(false));
  }, [token]);

  useEffect(() => {
    loadPlanAndUsage();
  }, [loadPlanAndUsage]);

  useEffect(() => {
    if (!token) return;
    getCurrentUser(token).then((me) => setIsAdmin(me.role === "owner" || me.role === "admin"));
  }, [token]);

  useEffect(() => {
    if (!token) return;
    listUsage(token)
      .then((data) => {
        setEvents(data);
        setLogError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 403) {
          setLogError("Usage is visible to account Owners and Admins only.");
        } else {
          setLogError("Couldn't load usage.");
        }
      })
      .finally(() => setLogLoading(false));
  }, [token]);

  async function handleChangePlan(planCode: string) {
    if (!token) return;
    setChangingPlan(planCode);
    setPlanError(null);
    try {
      await changeSubscriptionPlan(token, planCode);
      await loadPlanAndUsage();
    } catch (err) {
      setPlanError(err instanceof ApiError ? err.message : "Couldn't change plan.");
    } finally {
      setChangingPlan(null);
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Billing &amp; Usage</h2>
        <p className="text-sm text-slate-500">
          Plan limits and metered usage on your account. No payment is processed here yet — billing
          integration is still being connected.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-900">Plan &amp; Usage</h3>
          {subscription && (
            <span
              className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize ${
                subscription.status === "trialing"
                  ? "bg-amber-50 text-amber-700"
                  : subscription.status === "active"
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-red-50 text-red-700"
              }`}
            >
              {subscription.status}
            </span>
          )}
        </div>

        {summaryLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {summaryError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{summaryError}</p>}

        {subscription?.status === "past_due" && subscription.grace_period_ends_at && (
          <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            There&apos;s a problem with a recent payment. Outbound calling, video, new number purchases, and AI
            features will pause on{" "}
            {new Date(subscription.grace_period_ends_at).toLocaleDateString()} unless this is resolved. Incoming
            calls and your existing numbers are not affected.
          </p>
        )}

        {usageSummary && subscription && (
          <>
            <div className="flex items-baseline justify-between">
              <div>
                <div className="text-lg font-semibold text-slate-900">{usageSummary.plan_name}</div>
                <div className="text-xs text-slate-500 mt-0.5">
                  {subscription.status === "trialing" && subscription.trial_ends_at
                    ? `Trial ends ${new Date(subscription.trial_ends_at).toLocaleDateString()}`
                    : `Current period renews ${new Date(usageSummary.current_period_end).toLocaleDateString()}`}
                </div>
              </div>
            </div>

            <div className="space-y-3 pt-1">
              {usageSummary.resources.map((r) => (
                <UsageBar key={r.resource} resource={r} />
              ))}
            </div>
          </>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Plans</h3>
        {planError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{planError}</p>}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {plans.map((plan) => {
            const isCurrent = subscription?.plan_code === plan.plan_code;
            return (
              <div
                key={plan.plan_code}
                className={`rounded-lg border p-4 space-y-2 ${
                  isCurrent ? "border-indigo-300 bg-indigo-50/40" : "border-slate-200"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-slate-900">{plan.name}</span>
                  {isCurrent && (
                    <span className="text-xs font-medium text-indigo-700 bg-indigo-100 rounded-full px-2 py-0.5">
                      Current
                    </span>
                  )}
                </div>
                <ul className="text-xs text-slate-500 space-y-0.5">
                  <li>{plan.max_numbers.toLocaleString()} numbers</li>
                  <li>{plan.max_team_seats.toLocaleString()} team seats</li>
                  <li>{plan.monthly_voice_minutes.toLocaleString()} voice minutes / mo</li>
                  <li>{plan.monthly_video_minutes.toLocaleString()} video minutes / mo</li>
                  <li>{plan.monthly_ai_summaries.toLocaleString()} AI summaries / mo</li>
                </ul>
                {isAdmin && !isCurrent && (
                  <button
                    onClick={() => handleChangePlan(plan.plan_code)}
                    disabled={changingPlan === plan.plan_code}
                    className="w-full text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
                  >
                    {changingPlan === plan.plan_code ? "Switching..." : "Switch to this plan"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
        {!isAdmin && (
          <p className="text-xs text-slate-400">Only an account Owner or Admin can change plans.</p>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Usage log</h3>

        {logLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {logError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{logError}</p>}
        {!logLoading && !logError && events.length === 0 && (
          <p className="text-sm text-slate-500">No metered usage yet.</p>
        )}

        <div className="space-y-2">
          {events.map((e) => (
            <div
              key={e.id}
              className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3 text-sm"
            >
              <div>
                <span className="font-medium text-slate-800 capitalize">
                  {e.event_type.replaceAll("_", " ")}
                </span>
                {e.country_band && <span className="ml-2 text-xs text-slate-400">{e.country_band}</span>}
              </div>
              <div className="flex items-center gap-4">
                <span className="text-slate-600">{formatQuantity(e)}</span>
                <span className="text-xs text-slate-400">{new Date(e.created_at).toLocaleString()}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
