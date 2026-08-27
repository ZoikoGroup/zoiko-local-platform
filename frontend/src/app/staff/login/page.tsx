"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { staffLogin, ApiError } from "@/lib/api";
import { saveStaffToken } from "@/lib/staffAuth";
import "./../staff-theme.css";

const CONSOLE_AREAS = [
  "Cases & escalations",
  "Number provisioning",
  "Porting & compliance",
  "Fraud & risk review",
  "Billing operations",
  "Incidents & audit log",
];

function EyeIcon({ off = false }: { off?: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-[18px] w-[18px]"
      aria-hidden
    >
      {off ? (
        <>
          <path d="M3 3l18 18" />
          <path d="M10.6 5.2A9.9 9.9 0 0112 5c5 0 9 4.5 9 7 0 .9-.5 2-1.5 3.1" />
          <path d="M6.3 6.5C3.9 8 2 10.3 2 12c0 2.5 4 7 10 7 1.6 0 3-.3 4.2-.8" />
          <path d="M9.9 9.9a3 3 0 004.2 4.2" />
        </>
      ) : (
        <>
          <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
          <circle cx="12" cy="12" r="3" />
        </>
      )}
    </svg>
  );
}

export default function StaffLoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [capsOn, setCapsOn] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await staffLogin({ email, password });
      saveStaffToken(access_token);
      router.push("/staff/cases");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  const fieldClass =
    "w-full rounded-lg border border-slate-300 bg-white text-slate-900 px-3.5 py-2.5 text-sm placeholder:text-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 disabled:bg-slate-50 transition";

  return (
    <main className="staff-scope glass-dark min-h-screen bg-slate-950 px-4 py-10 flex items-center justify-center">
      {/* Soft brand glow behind the card - same treatment as the marketing
          hero, keeps the flat green from looking like an error page. */}
      <div
        aria-hidden
        className="pointer-events-none fixed -top-40 left-1/2 h-[520px] w-[820px] -translate-x-1/2 rounded-full bg-indigo-600/10 blur-3xl"
      />

      <div className="relative w-full max-w-5xl overflow-hidden rounded-2xl border border-white/10 bg-slate-900 shadow-2xl shadow-black/40 lg:grid lg:grid-cols-[1.05fr_1fr]">
        {/* ── BRAND / CONTEXT PANEL ─────────────────────────────────── */}
        <aside className="relative flex flex-col justify-between gap-10 border-b border-white/10 p-8 lg:border-b-0 lg:border-r lg:p-10">
          <div>
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 text-base font-bold text-white shadow-lg shadow-indigo-600/25">
                Z
              </div>
              <div className="leading-tight">
                <div className="font-semibold text-white">Zoiko Local</div>
                <div className="text-xs text-white/55">Internal Ops Console</div>
              </div>
            </div>

            <h2 className="mt-8 text-2xl font-semibold leading-snug text-white">
              Operations, provisioning
              <br />
              and compliance control.
            </h2>
            <p className="mt-3 max-w-sm text-sm leading-relaxed text-white/60">
              One console for the teams running the platform — number supply,
              customer escalations, risk review and the audit trail behind
              every action.
            </p>
          </div>

          <ul className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
            {CONSOLE_AREAS.map((area) => (
              <li key={area} className="flex items-start gap-2.5 text-sm text-white/75">
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="mt-[5px] h-3 w-3 shrink-0 text-emerald-400"
                  aria-hidden
                >
                  <path d="M20 6 9 17l-5-5" />
                </svg>
                {area}
              </li>
            ))}
          </ul>

          <div className="flex flex-wrap items-center gap-2 border-t border-white/10 pt-6">
            <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.06] px-3 py-1 font-mono text-[11px] text-white/70">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              staff-api
            </span>
            <span className="rounded-full border border-white/15 bg-white/[0.06] px-3 py-1 font-mono text-[11px] text-white/70">
              SSO not enabled
            </span>
          </div>
        </aside>

        {/* ── SIGN-IN PANEL ──────────────────────────────────────────── */}
        <section className="bg-slate-50 p-8 lg:p-10">
          <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-4 w-4 shrink-0 text-amber-700"
              aria-hidden
            >
              <path d="M12 3l9 16H3l9-16Z" />
              <path d="M12 9v4M12 16h.01" />
            </svg>
            <p className="text-xs font-medium text-amber-800">
              Restricted — Zoiko personnel only. All sign-ins are logged.
            </p>
          </div>

          <h1 className="mt-6 text-xl font-semibold text-slate-900">Staff sign-in</h1>
          <p className="mt-1 text-sm text-slate-500">
            Not a customer login.{" "}
            <Link href="/login" className="font-medium text-indigo-600 hover:underline">
              Go to customer sign-in
            </Link>
          </p>

          <form onSubmit={handleSubmit} className="mt-7 space-y-4">
            {/* Disabling the whole set while in flight beats disabling only
                the button - stops a second submit via Enter from a field. */}
            <fieldset disabled={loading} className="space-y-4">
              <div>
                <label
                  htmlFor="staff-email"
                  className="mb-1.5 block text-sm font-medium text-slate-700"
                >
                  Work email
                </label>
                <input
                  id="staff-email"
                  type="email"
                  required
                  autoFocus
                  autoComplete="username"
                  spellCheck={false}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  aria-invalid={error ? true : undefined}
                  className={fieldClass}
                  placeholder="you@zoiko.com"
                />
              </div>

              <div>
                <div className="mb-1.5 flex items-baseline justify-between gap-3">
                  <label htmlFor="staff-password" className="text-sm font-medium text-slate-700">
                    Password
                  </label>
                  {capsOn && (
                    <span className="text-[11px] font-medium text-amber-700">Caps Lock is on</span>
                  )}
                </div>
                <div className="relative">
                  <input
                    id="staff-password"
                    type={showPassword ? "text" : "password"}
                    required
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyUp={(e) => setCapsOn(e.getModifierState?.("CapsLock") ?? false)}
                    aria-invalid={error ? true : undefined}
                    className={`${fieldClass} pr-11`}
                    placeholder="••••••••"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    tabIndex={-1}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-2 text-slate-400 transition hover:text-slate-600"
                  >
                    <EyeIcon off={showPassword} />
                  </button>
                </div>
              </div>
            </fieldset>

            {error && (
              <p
                role="alert"
                className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 py-2.5 text-sm font-semibold text-white shadow-sm shadow-indigo-600/25 transition hover:bg-indigo-700 disabled:opacity-60"
            >
              {loading && (
                <svg
                  viewBox="0 0 24 24"
                  className="h-4 w-4 animate-spin motion-reduce:animate-none"
                  aria-hidden
                >
                  <circle
                    cx="12"
                    cy="12"
                    r="9"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    strokeOpacity="0.3"
                  />
                  <path
                    d="M21 12a9 9 0 00-9-9"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    strokeLinecap="round"
                  />
                </svg>
              )}
              {loading ? "Signing in…" : "Sign in to console"}
            </button>
          </form>

          <p className="mt-6 border-t border-slate-200 pt-5 text-xs leading-relaxed text-slate-500">
            Access is role-scoped and every action is written to the audit log.
            Lost access or need a role change? Contact platform operations —
            there is no self-service reset for staff accounts.
          </p>
        </section>
      </div>
    </main>
  );
}