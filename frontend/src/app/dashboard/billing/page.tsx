"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getCurrentUser,
  listUsage,
  listPlans,
  getSubscription,
  changeSubscriptionPlan,
  createPlanChangeCheckoutSession,
  setAIReceptionistAddon,
  cancelSubscription,
  getPriceCatalogEntry,
  getUsageSummary,
  getAIUsageRate,
  getAIReceptionistAddonRate,
  ApiError,
  type UsageEvent,
  type Plan,
  type PriceCatalogEntry,
  type Subscription,
  type UsageSummary,
  type BillingPeriod,
  type AIUsageRate,
  type AIReceptionistAddonRate,
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

function formatPrice(entry: PriceCatalogEntry | null | undefined): string {
  // is_placeholder, not just null, must gate this - confirmed live that
  // get_active_price_catalog_entry's own fallback ("nothing activated yet,
  // use the most recently created entry regardless of status") can still
  // return an old fake test-placeholder row (e.g. Enterprise's leftover
  // $199.99 test price) even when a plan has no real decided price. A
  // placeholder is never a real price anywhere else in this codebase -
  // the customer-facing display must hold to that same rule.
  if (!entry || entry.is_placeholder) return "Custom";
  const amount = (entry.amount_minor_units / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const suffix = entry.billing_period === "annual" ? "/yr" : "/mo";
  return `${entry.currency_code} ${amount}${suffix}`;
}

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
  const [billingPeriod, setBillingPeriod] = useState<BillingPeriod>("monthly");
  const [prices, setPrices] = useState<Record<string, PriceCatalogEntry | null>>({});
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [changingPlan, setChangingPlan] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [confirmingPlan, setConfirmingPlan] = useState<string | null>(null);
  const [planChangedTo, setPlanChangedTo] = useState<string | null>(null);
  const [canceling, setCanceling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [confirmingCancel, setConfirmingCancel] = useState(false);

  const [aiUsageRate, setAiUsageRate] = useState<AIUsageRate | null>(null);
  const [addonRate, setAddonRate] = useState<AIReceptionistAddonRate | null>(null);
  const [addonBusy, setAddonBusy] = useState(false);
  const [addonError, setAddonError] = useState<string | null>(null);

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
    if (!token || plans.length === 0) return;
    Promise.all(
      plans.map((plan) =>
        getPriceCatalogEntry(token, plan.plan_code, billingPeriod)
          .then((entry) => [plan.plan_code, entry] as const)
          .catch(() => [plan.plan_code, null] as const)
      )
    ).then((entries) => {
      setPrices(Object.fromEntries(entries));
    });
  }, [token, plans, billingPeriod]);

  useEffect(() => {
    if (!token) return;
    getAIUsageRate(token)
      .then(setAiUsageRate)
      .catch(() => {});
    getAIReceptionistAddonRate(token)
      .then(setAddonRate)
      .catch(() => {});
  }, [token]);

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
      const entry = prices[planCode];
      const requiresPayment = !!entry && !entry.is_placeholder && entry.amount_minor_units > 0;
      if (requiresPayment) {
        // Real money changes hands here - redirect to Stripe's hosted
        // Checkout page instead of switching the plan locally. The plan
        // itself only changes once Stripe confirms payment via the
        // /billing/stripe/checkout-webhook backend route, not on this
        // click - so we navigate away and never reach loadPlanAndUsage.
        const session = await createPlanChangeCheckoutSession(token, planCode, billingPeriod);
        window.location.href = session.url;
        return;
      }
      await changeSubscriptionPlan(token, planCode, billingPeriod);
      await loadPlanAndUsage();
      setConfirmingPlan(null);
      setPlanChangedTo(planCode);
    } catch (err) {
      setPlanError(err instanceof ApiError ? err.message : "Couldn't change plan.");
    } finally {
      setChangingPlan(null);
    }
  }

  async function handleToggleAddon(active: boolean) {
    if (!token) return;
    setAddonBusy(true);
    setAddonError(null);
    try {
      const sub = await setAIReceptionistAddon(token, active);
      setSubscription(sub);
    } catch (err) {
      setAddonError(err instanceof ApiError ? err.message : "Couldn't update the AI Receptionist add-on.");
    } finally {
      setAddonBusy(false);
    }
  }

  async function handleCancelSubscription() {
    if (!token) return;
    setCanceling(true);
    setCancelError(null);
    try {
      await cancelSubscription(token);
      await loadPlanAndUsage();
      setConfirmingCancel(false);
    } catch (err) {
      setCancelError(err instanceof ApiError ? err.message : "Couldn't cancel subscription.");
    } finally {
      setCanceling(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Billing &amp; Usage</h2>
        <p className="text-sm text-slate-500">
          Plan limits, pricing and metered usage on your account.
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

        {subscription?.status === "canceled" && (
          <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            This subscription was canceled{" "}
            {subscription.canceled_at ? `on ${new Date(subscription.canceled_at).toLocaleDateString()}` : ""}.
            Outbound calling, video, new number purchases, and AI features are paused. Choose a plan below to
            resume.
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
              {prices[usageSummary.plan_code] !== undefined && (
                <div className="text-lg font-semibold text-slate-900">
                  {formatPrice(prices[usageSummary.plan_code])}
                </div>
              )}
            </div>

            <div className="space-y-3 pt-1">
              {usageSummary.resources.map((r) => (
                <UsageBar key={r.resource} resource={r} />
              ))}
            </div>
          </>
        )}

        {isAdmin && subscription && subscription.status !== "canceled" && (
          <div className="border-t border-slate-100 pt-4">
            {cancelError && (
              <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2 mb-2">{cancelError}</p>
            )}
            {!confirmingCancel ? (
              <button
                onClick={() => setConfirmingCancel(true)}
                className="text-xs font-medium text-red-600 hover:text-red-700"
              >
                Cancel subscription
              </button>
            ) : (
              <div className="rounded-lg border border-red-200 bg-red-50 p-3 space-y-2">
                <p className="text-sm text-red-800">
                  This takes effect immediately - outbound calling, video, new number purchases, and AI
                  features will stop. This can&apos;t be undone from here.
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={handleCancelSubscription}
                    disabled={canceling}
                    className="text-xs font-medium rounded-lg px-3 py-1.5 bg-red-600 hover:bg-red-700 disabled:opacity-60 text-white"
                  >
                    {canceling ? "Canceling..." : "Yes, cancel subscription"}
                  </button>
                  <button
                    onClick={() => setConfirmingCancel(false)}
                    disabled={canceling}
                    className="text-xs font-medium rounded-lg px-3 py-1.5 border border-slate-300 text-slate-700 hover:bg-slate-50"
                  >
                    Keep subscription
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-900">Plans</h3>
          <div className="inline-flex items-center rounded-lg border border-slate-200 p-0.5 text-xs font-medium">
            <button
              onClick={() => setBillingPeriod("monthly")}
              className={`rounded-md px-3 py-1 ${
                billingPeriod === "monthly" ? "bg-indigo-600 text-white" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setBillingPeriod("annual")}
              className={`rounded-md px-3 py-1 ${
                billingPeriod === "annual" ? "bg-indigo-600 text-white" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Annual <span className="text-emerald-600">(save ~17%)</span>
            </button>
          </div>
        </div>
        {planError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{planError}</p>}
        {planChangedTo && (
          <p className="text-sm text-emerald-700 bg-emerald-50 rounded-lg px-3 py-2">
            You&apos;re now on the {plans.find((p) => p.plan_code === planChangedTo)?.name ?? planChangedTo} plan.
          </p>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {plans.map((plan) => {
            const isCurrent = subscription?.plan_code === plan.plan_code;
            const isConfirming = confirmingPlan === plan.plan_code;
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
                <div className="text-sm font-semibold text-slate-800">
                  {formatPrice(prices[plan.plan_code])}
                </div>
                <ul className="text-xs text-slate-500 space-y-0.5">
                  <li>{plan.max_numbers.toLocaleString()} numbers</li>
                  <li>{plan.max_team_seats.toLocaleString()} team seats</li>
                  <li>{plan.monthly_voice_minutes.toLocaleString()} voice minutes / mo</li>
                  <li>{plan.monthly_video_minutes.toLocaleString()} video minutes / mo</li>
                  <li>{plan.monthly_ai_summaries.toLocaleString()} AI summaries / mo</li>
                  {plan.included_ai_receptionist_minutes > 0 && (
                    <li>{plan.included_ai_receptionist_minutes.toLocaleString()} AI Receptionist minutes / mo</li>
                  )}
                </ul>
                {isAdmin && !isCurrent && !isConfirming && (
                  <button
                    onClick={() => {
                      setPlanChangedTo(null);
                      setConfirmingPlan(plan.plan_code);
                    }}
                    className="w-full text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
                  >
                    Switch to this plan
                  </button>
                )}
                {isAdmin && isConfirming && (
                  <div className="space-y-1.5">
                    <p className="text-xs text-slate-600">
                      Move from {subscription ? plans.find((p) => p.plan_code === subscription.plan_code)?.name : "your current plan"} to{" "}
                      {plan.name} ({formatPrice(prices[plan.plan_code])})?
                    </p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleChangePlan(plan.plan_code)}
                        disabled={changingPlan === plan.plan_code}
                        className="flex-1 text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
                      >
                        {changingPlan === plan.plan_code ? "Switching..." : "Confirm"}
                      </button>
                      <button
                        onClick={() => setConfirmingPlan(null)}
                        disabled={changingPlan === plan.plan_code}
                        className="flex-1 text-xs font-medium rounded-lg px-3 py-1.5 border border-slate-300 text-slate-700 hover:bg-slate-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {!isAdmin && (
          <p className="text-xs text-slate-400">Only an account Owner or Admin can change plans.</p>
        )}
      </div>

      {addonRate && subscription && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-slate-900">AI Receptionist add-on</h3>
              <p className="text-sm text-slate-500">
                {(addonRate.monthly_price_minor_units / 100).toFixed(2)} {addonRate.currency_code}/workspace/month —{" "}
                {addonRate.included_minutes} AI-handled minutes included, then{" "}
                {(addonRate.overage_rate_minor_units_per_minute / 100).toFixed(2)} {addonRate.currency_code}/min.
              </p>
            </div>
            {subscription.ai_receptionist_addon_enabled && (
              <span className="text-xs font-medium text-emerald-700 bg-emerald-50 rounded-full px-2.5 py-1">
                Active
              </span>
            )}
          </div>
          {addonError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{addonError}</p>}
          {isAdmin ? (
            <button
              onClick={() => handleToggleAddon(!subscription.ai_receptionist_addon_enabled)}
              disabled={addonBusy}
              className={`text-xs font-medium rounded-lg px-3 py-1.5 disabled:opacity-60 ${
                subscription.ai_receptionist_addon_enabled
                  ? "border border-slate-300 text-slate-700 hover:bg-slate-50"
                  : "bg-indigo-600 hover:bg-indigo-700 text-white"
              }`}
            >
              {addonBusy
                ? "Saving..."
                : subscription.ai_receptionist_addon_enabled
                  ? "Remove add-on"
                  : "Add to my plan"}
            </button>
          ) : (
            <p className="text-xs text-slate-400">Only an account Owner or Admin can change add-ons.</p>
          )}
        </div>
      )}

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
