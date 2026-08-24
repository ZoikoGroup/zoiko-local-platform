"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  listPlans,
  getPriceCatalogEntry,
  changeSubscriptionPlan,
  ApiError,
  type Plan,
  type PriceCatalogEntry,
  type BillingPeriod,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

// Global Plans, Pricing & Commercial Launch Standard doc §8.2 - Enterprise
// is sales-led, never a self-serve "switch to this plan" action.
const SALES_LED_PLAN_CODE = "enterprise";

// A blank grid of plan names and numbers gives a new customer nothing to
// anchor a decision on. These map straight to the doc's own "Plan
// positioning copy" (§4) for each tier - Pro is explicitly the AI/
// customer-facing tier, Scale is explicitly the multi-market/high-volume
// tier - so the recommendation isn't a guess, it's the doc's own intent
// surfaced as a question instead of left implicit in a features table.
const USE_CASES: { id: string; label: string; description: string; recommends: string }[] = [
  {
    id: "solo",
    label: "Just me",
    description: "One person who needs a professional local number to call and connect.",
    recommends: "starter",
  },
  {
    id: "team",
    label: "A small team",
    description: "A few people making and receiving calls together, with shared routing.",
    recommends: "business",
  },
  {
    id: "ai_reception",
    label: "AI reception for customer calls",
    description: "A customer-facing team that wants AI to answer, qualify and route calls automatically.",
    recommends: "pro",
  },
  {
    id: "call_center",
    label: "A call center / high call volume",
    description: "Multiple markets, many numbers, and high call volume across a larger team.",
    recommends: "scale",
  },
  {
    id: "enterprise",
    label: "Large or regulated organization",
    description: "A custom, contracted deployment with compliance or scale needs beyond a self-serve plan.",
    recommends: "enterprise",
  },
];

function formatPrice(entry: PriceCatalogEntry | null | undefined): string {
  if (!entry || entry.is_placeholder) return "Custom";
  const amount = (entry.amount_minor_units / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const suffix = entry.billing_period === "annual" ? "/yr" : "/mo";
  return `${entry.currency_code} ${amount}${suffix}`;
}

export default function ChoosePlanPage() {
  const router = useRouter();
  const [token] = useState<string | null>(() => getToken());
  const [plans, setPlans] = useState<Plan[]>([]);
  const [billingPeriod, setBillingPeriod] = useState<BillingPeriod>("monthly");
  const [prices, setPrices] = useState<Record<string, PriceCatalogEntry | null>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [choosing, setChoosing] = useState<string | null>(null);
  const [chooseError, setChooseError] = useState<string | null>(null);
  const [selectedUseCase, setSelectedUseCase] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      router.replace("/login");
      return;
    }
  }, [token, router]);

  const loadPlans = useCallback(() => {
    if (!token) return;
    listPlans(token)
      .then((planList) => {
        // free_trial isn't a choice on this screen - it's what every new
        // account already starts on. Showing it as a selectable card would
        // just be a confusing no-op "switch to the plan you're already on".
        setPlans(planList.filter((p) => p.plan_code !== "free_trial"));
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load plans."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadPlans();
  }, [loadPlans]);

  useEffect(() => {
    if (!token || plans.length === 0) return;
    Promise.all(
      plans.map((plan) =>
        getPriceCatalogEntry(token, plan.plan_code, billingPeriod)
          .then((entry) => [plan.plan_code, entry] as const)
          .catch(() => [plan.plan_code, null] as const)
      )
    ).then((entries) => setPrices(Object.fromEntries(entries)));
  }, [token, plans, billingPeriod]);

  async function handleChoose(planCode: string) {
    if (!token) return;
    setChoosing(planCode);
    setChooseError(null);
    try {
      await changeSubscriptionPlan(token, planCode, billingPeriod);
      router.push("/dashboard");
    } catch (err) {
      setChooseError(err instanceof ApiError ? err.message : "Couldn't set your plan.");
      setChoosing(null);
    }
  }

  function handleSkip() {
    router.push("/dashboard");
  }

  const recommendedPlanCode = USE_CASES.find((u) => u.id === selectedUseCase)?.recommends ?? null;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-5xl mx-auto px-6 py-12">
        <div className="flex items-center gap-2 mb-10">
          <img src="/logo-icon.svg" alt="Zoiko Local" className="w-9 h-9 rounded-lg" />
          <div className="font-semibold text-slate-900">Zoiko Local</div>
        </div>

        <div className="text-center mb-8">
          <h1 className="text-2xl font-semibold text-slate-900">Choose a plan to get started</h1>
          <p className="text-sm text-slate-500 mt-2 max-w-xl mx-auto">
            Your account is set up. Not sure which plan fits? Tell us what you need it for and we&apos;ll
            point you at one — or compare all of them below and pick yourself.
          </p>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5 mb-8">
          <div className="text-sm font-medium text-slate-900 mb-3">What are you using Zoiko Local for?</div>
          <div className="flex flex-wrap gap-2">
            {USE_CASES.map((useCase) => (
              <button
                key={useCase.id}
                onClick={() => setSelectedUseCase(useCase.id)}
                title={useCase.description}
                className={`text-xs font-medium rounded-full px-3.5 py-2 border transition ${
                  selectedUseCase === useCase.id
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-slate-300 text-slate-600 hover:bg-slate-50"
                }`}
              >
                {useCase.label}
              </button>
            ))}
          </div>
          {selectedUseCase && (
            <p className="text-xs text-slate-500 mt-3">
              {USE_CASES.find((u) => u.id === selectedUseCase)?.description} We&apos;ve marked the plan built
              for that below.
            </p>
          )}
        </div>

        <div className="flex justify-center mb-6">
          <div className="inline-flex items-center rounded-lg border border-slate-200 p-0.5 text-xs font-medium bg-white">
            <button
              onClick={() => setBillingPeriod("monthly")}
              className={`rounded-md px-3 py-1.5 ${
                billingPeriod === "monthly" ? "bg-indigo-600 text-white" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setBillingPeriod("annual")}
              className={`rounded-md px-3 py-1.5 ${
                billingPeriod === "annual" ? "bg-indigo-600 text-white" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Annual <span className="text-emerald-600">(save ~17%)</span>
            </button>
          </div>
        </div>

        {loading && <p className="text-sm text-slate-500 text-center">Loading plans...</p>}
        {loadError && (
          <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2 max-w-md mx-auto text-center">
            {loadError}
          </p>
        )}
        {chooseError && (
          <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2 max-w-md mx-auto text-center mb-6">
            {chooseError}
          </p>
        )}

        {!loading && !loadError && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
            {plans.map((plan) => {
              const isSalesLed = plan.plan_code === SALES_LED_PLAN_CODE;
              const isRecommended = recommendedPlanCode === plan.plan_code;
              // No use case picked yet - fall back to the doc's own default
              // "Most Popular" badge (Business) rather than showing nothing.
              const showAsPopularDefault = !recommendedPlanCode && plan.plan_code === "business";
              return (
                <div
                  key={plan.plan_code}
                  className={`relative bg-white rounded-xl border p-5 space-y-3 flex flex-col ${
                    isRecommended
                      ? "border-indigo-500 ring-2 ring-indigo-300"
                      : showAsPopularDefault
                        ? "border-indigo-300 ring-1 ring-indigo-200"
                        : "border-slate-200"
                  }`}
                >
                  {(isRecommended || showAsPopularDefault) && (
                    <span className="absolute -top-2.5 left-1/2 -translate-x-1/2 text-[10px] font-medium tracking-wide uppercase text-white bg-indigo-600 rounded-full px-2.5 py-0.5 whitespace-nowrap">
                      {isRecommended ? "Recommended for you" : "Most popular"}
                    </span>
                  )}
                  <div className="font-semibold text-slate-900">{plan.name}</div>
                  <div className="text-lg font-semibold text-slate-900">{formatPrice(prices[plan.plan_code])}</div>
                  <ul className="text-xs text-slate-500 space-y-1 flex-1">
                    <li>{plan.max_numbers.toLocaleString()} local number{plan.max_numbers === 1 ? "" : "s"}</li>
                    <li>{plan.max_team_seats.toLocaleString()} team seats</li>
                    <li>{plan.monthly_voice_minutes.toLocaleString()} voice minutes / mo</li>
                    <li>{plan.monthly_video_minutes.toLocaleString()} video minutes / mo</li>
                    <li>{plan.monthly_ai_summaries.toLocaleString()} AI summaries / mo</li>
                  </ul>
                  {isSalesLed ? (
                    <a
                      href="mailto:support@zoikolocal.com?subject=Enterprise%20plan"
                      className="w-full text-center text-xs font-medium rounded-lg px-3 py-2 border border-slate-300 text-slate-700 hover:bg-slate-50"
                    >
                      Contact Sales
                    </a>
                  ) : (
                    <button
                      onClick={() => handleChoose(plan.plan_code)}
                      disabled={choosing !== null}
                      className={`w-full text-xs font-medium rounded-lg px-3 py-2 disabled:opacity-60 text-white ${
                        isRecommended ? "bg-indigo-600 hover:bg-indigo-700" : "bg-slate-800 hover:bg-slate-900"
                      }`}
                    >
                      {choosing === plan.plan_code ? "Setting up..." : `Choose ${plan.name}`}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="text-center mt-10">
          <button
            onClick={handleSkip}
            disabled={choosing !== null}
            className="text-sm font-medium text-slate-500 hover:text-slate-700 disabled:opacity-60"
          >
            Continue with 14-day free trial instead →
          </button>
        </div>
      </div>
    </div>
  );
}
