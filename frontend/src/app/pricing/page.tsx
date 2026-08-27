"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  listPublicPlans,
  getPublicPlanPrice,
  listPublicCountries,
  type Plan,
  type PriceCatalogEntry,
  type BillingPeriod,
  type PublicSupportedCountry,
} from "@/lib/api";
import Logo from "@/components/Logo";

// Global Plans, Pricing & Commercial Launch Standard doc §8.2 - Enterprise
// is sales-led/custom, never shown with a dollar amount or precise limits.
const SALES_LED_PLAN_CODE = "enterprise";
const MOST_POPULAR_PLAN_CODE = "business";

function formatPrice(entry: PriceCatalogEntry | null | undefined): string {
  if (!entry || entry.is_placeholder) return "Custom";
  const amount = (entry.amount_minor_units / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const suffix = entry.billing_period === "annual" ? "/user/year" : "/user/month";
  return `$${amount}${suffix}`;
}

// Comparison table rows - limited to fields that actually exist on Plan.
// Rows like "API & webhooks" or "Team administration" tiers from the
// original design have no backing field anywhere in this codebase and are
// deliberately not shown here rather than inventing values for them.
const COMPARISON_ROWS: { label: string; get: (p: Plan) => string }[] = [
  { label: "Local numbers", get: (p) => p.max_numbers.toLocaleString() },
  { label: "Team seats", get: (p) => p.max_team_seats.toLocaleString() },
  { label: "Voice minutes / month", get: (p) => p.monthly_voice_minutes.toLocaleString() },
  { label: "Video minutes / month", get: (p) => p.monthly_video_minutes.toLocaleString() },
  { label: "AI summaries / month", get: (p) => p.monthly_ai_summaries.toLocaleString() },
  {
    label: "AI Receptionist minutes",
    get: (p) => (p.included_ai_receptionist_minutes > 0 ? `${p.included_ai_receptionist_minutes.toLocaleString()} incl.` : "Add-on"),
  },
];

export default function PricingPage() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [billingPeriod, setBillingPeriod] = useState<BillingPeriod>("monthly");
  const [prices, setPrices] = useState<Record<string, PriceCatalogEntry | null>>({});
  const [countries, setCountries] = useState<PublicSupportedCountry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    listPublicPlans()
      .then((planList) => {
        // free_trial isn't a purchasable tier - it's what a new signup
        // starts on automatically, not a card to choose here.
        setPlans(planList.filter((p) => p.plan_code !== "free_trial"));
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load plans."))
      .finally(() => setLoading(false));
    listPublicCountries()
      .then(setCountries)
      .catch(() => setCountries([]));
  }, []);

  useEffect(() => {
    if (plans.length === 0) return;
    Promise.all(
      plans.map((plan) =>
        getPublicPlanPrice(plan.plan_code, billingPeriod)
          .then((entry) => [plan.plan_code, entry] as const)
          .catch(() => [plan.plan_code, null] as const)
      )
    ).then((entries) => setPrices(Object.fromEntries(entries)));
  }, [plans, billingPeriod]);

  return (
    <div className="glass-ui min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <Logo size={32} />
            <span className="font-semibold text-slate-900">Zoiko Local</span>
          </Link>
          <div className="flex items-center gap-3">
            <Link href="/login" className="text-sm font-medium text-slate-600 hover:text-slate-900">
              Sign In
            </Link>
            <Link
              href="/signup"
              className="text-sm font-medium rounded-lg px-4 py-2 bg-indigo-600 text-white hover:bg-indigo-700"
            >
              Start Free
            </Link>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-16">
        <div className="text-center mb-10">
          <p className="text-xs font-semibold tracking-wide uppercase text-indigo-600 mb-3">Plans &amp; Pricing</p>
          <h1 className="text-3xl sm:text-4xl font-semibold text-slate-900">
            Pricing that stays simple as your reach grows.
          </h1>
          <p className="text-sm text-slate-500 mt-4 max-w-xl mx-auto">
            Start with a local number. Add your team, new markets and AI when you need them.
          </p>
        </div>

        <div className="flex justify-center mb-10">
          <div className="inline-flex items-center rounded-lg border border-slate-200 p-0.5 text-xs font-medium bg-white">
            <button
              onClick={() => setBillingPeriod("monthly")}
              className={`rounded-md px-4 py-1.5 ${
                billingPeriod === "monthly" ? "bg-indigo-600 text-white" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setBillingPeriod("annual")}
              className={`rounded-md px-4 py-1.5 ${
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

        {!loading && !loadError && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 mb-16">
            {plans.map((plan) => {
              const isSalesLed = plan.plan_code === SALES_LED_PLAN_CODE;
              const isPopular = plan.plan_code === MOST_POPULAR_PLAN_CODE;
              return (
                <div
                  key={plan.plan_code}
                  className={`relative bg-white rounded-xl border p-5 space-y-3 flex flex-col ${
                    isPopular ? "border-indigo-500 ring-2 ring-indigo-300" : "border-slate-200"
                  }`}
                >
                  {isPopular && (
                    <span className="absolute -top-2.5 left-1/2 -translate-x-1/2 text-[10px] font-medium tracking-wide uppercase text-white bg-indigo-600 rounded-full px-2.5 py-0.5 whitespace-nowrap">
                      Most popular
                    </span>
                  )}
                  <div className="font-semibold text-slate-900">{plan.name}</div>
                  <div className="text-lg font-semibold text-slate-900">{formatPrice(prices[plan.plan_code])}</div>
                  <ul className="text-xs text-slate-500 space-y-1 flex-1">
                    {isSalesLed ? (
                      <>
                        <li>Negotiated market &amp; number capacity</li>
                        <li>Volume rate cards where approved</li>
                        <li>Custom AI Receptionist allowance</li>
                        <li>Security, compliance &amp; procurement review</li>
                      </>
                    ) : (
                      <>
                        <li>{plan.max_numbers.toLocaleString()} local number{plan.max_numbers === 1 ? "" : "s"}</li>
                        <li>{plan.max_team_seats.toLocaleString()} team seats</li>
                        <li>{plan.monthly_voice_minutes.toLocaleString()} voice minutes / mo</li>
                        <li>{plan.monthly_video_minutes.toLocaleString()} video minutes / mo</li>
                        <li>
                          {plan.included_ai_receptionist_minutes > 0
                            ? `${plan.included_ai_receptionist_minutes.toLocaleString()} AI Receptionist minutes / mo`
                            : "AI Receptionist available as an add-on"}
                        </li>
                      </>
                    )}
                  </ul>
                  {isSalesLed ? (
                    <a
                      href="mailto:support@zoikolocal.com?subject=Enterprise%20plan"
                      className="w-full text-center text-xs font-medium rounded-lg px-3 py-2 border border-slate-300 text-slate-700 hover:bg-slate-50"
                    >
                      Contact Sales
                    </a>
                  ) : (
                    <Link
                      href="/signup"
                      className={`w-full text-center text-xs font-medium rounded-lg px-3 py-2 text-white ${
                        isPopular ? "bg-indigo-600 hover:bg-indigo-700" : "bg-slate-800 hover:bg-slate-900"
                      }`}
                    >
                      Start Free
                    </Link>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {!loading && !loadError && (
          <div className="mb-16">
            <h2 className="text-xl font-semibold text-slate-900 text-center mb-6">Compare every entitlement.</h2>
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="text-left font-medium text-slate-500 px-4 py-3">Capability</th>
                    {plans.map((plan) => (
                      <th
                        key={plan.plan_code}
                        className={`text-left font-medium px-4 py-3 ${
                          plan.plan_code === MOST_POPULAR_PLAN_CODE ? "text-indigo-600" : "text-slate-500"
                        }`}
                      >
                        {plan.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {COMPARISON_ROWS.map((row) => (
                    <tr key={row.label} className="border-b border-slate-100 last:border-0">
                      <td className="px-4 py-3 font-medium text-slate-700">{row.label}</td>
                      {plans.map((plan) => (
                        <td key={plan.plan_code} className="px-4 py-3 text-slate-600">
                          {plan.plan_code === SALES_LED_PLAN_CODE ? "Custom" : row.get(plan)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {countries.length > 0 && (
          <div className="rounded-xl bg-slate-900 text-white p-10 text-center mb-4">
            <p className="text-xs font-semibold tracking-wide uppercase text-indigo-300 mb-3">Ready when you are</p>
            <h2 className="text-2xl font-semibold mb-6">Get your local number where business and life happen.</h2>
            <Link
              href="/signup"
              className="inline-block text-sm font-medium rounded-lg px-5 py-2.5 bg-indigo-500 hover:bg-indigo-400 mb-6"
            >
              Get a Local Number →
            </Link>
            <div className="flex flex-wrap justify-center gap-2">
              {countries.map((c) => (
                <span key={c.code} className="text-xs font-medium rounded-full px-3.5 py-2 border border-slate-700 text-slate-200">
                  {c.name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <footer className="border-t border-slate-200 bg-white">
        <div className="max-w-6xl mx-auto px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Logo size={28} />
            <span className="text-sm font-medium text-slate-700">Zoiko Local</span>
          </div>
          <div className="flex items-center gap-6 text-sm text-slate-500">
            <Link href="/signup" className="hover:text-slate-900">Sign Up</Link>
            <Link href="/login" className="hover:text-slate-900">Sign In</Link>
            <Link href="/status" className="hover:text-slate-900">Status</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
