"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { login, googleAuth, completeMfaLogin, ApiError } from "@/lib/api";
import { saveToken } from "@/lib/auth";
import AuthLayout from "@/components/AuthLayout";
import GoogleSignInButton from "@/components/GoogleSignInButton";
import PasswordInput from "@/components/PasswordInput";

const iconProps = { width: 20, height: 20, viewBox: "0 0 24 24", "aria-hidden": true } as const;

const MICROSOFT = {
  label: "Continue with Microsoft",
  icon: (
    <svg {...iconProps}>
      <rect x="2" y="2" width="9.5" height="9.5" fill="#f25022" />
      <rect x="12.5" y="2" width="9.5" height="9.5" fill="#7fba00" />
      <rect x="2" y="12.5" width="9.5" height="9.5" fill="#00a4ef" />
      <rect x="12.5" y="12.5" width="9.5" height="9.5" fill="#ffb900" />
    </svg>
  ),
};
const APPLE = {
  label: "Continue with Apple",
  icon: (
    <svg {...iconProps} fill="currentColor">
      <path d="M16.4 12.6c0-2.4 2-3.5 2.1-3.6-1.1-1.7-2.9-1.9-3.5-1.9-1.5-.2-2.9.9-3.7.9-.8 0-1.9-.9-3.2-.8-1.6 0-3.1 1-4 2.4-1.7 3-.4 7.4 1.2 9.8.8 1.2 1.8 2.5 3 2.4 1.2 0 1.7-.8 3.1-.8s1.9.8 3.2.8c1.3 0 2.1-1.2 2.9-2.3.9-1.3 1.3-2.6 1.3-2.7-.1 0-2.4-.9-2.4-3.6zM14 5.5c.7-.8 1.1-1.9 1-3-1 0-2.1.7-2.8 1.5-.6.7-1.1 1.8-1 2.9 1.1.1 2.1-.6 2.8-1.4z" />
    </svg>
  ),
};
const SSO = {
  label: "Continue with Company SSO",
  icon: (
    <svg {...iconProps} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="3" width="16" height="18" rx="1.5" />
      <path d="M9 21v-4h6v4M8.5 7.5h.01M12 7.5h.01M15.5 7.5h.01M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01" />
    </svg>
  ),
};

// Same sizing for every field/button so the stack stays even, scaled by the
// viewport height so the whole screen fits without scrolling.
const FIELD_H = "h-[clamp(2.25rem,5.1vh,2.875rem)]";
const INPUT =
  `w-full ${FIELD_H} rounded-lg border border-slate-200 bg-white pl-10 pr-3 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition`;
const LABEL = "block text-[clamp(12px,1.75vh,14px)] font-semibold text-slate-800 mb-[0.5vh]";

// No backend for these providers yet (only Google is implemented) - shown
// disabled rather than as buttons that do nothing. On short windows
// (`tiny:`) they collapse into one icon-only row to save vertical space.
function ComingSoonRow({ label, icon }: { label: string; icon: React.ReactNode }) {
  return (
    <button
      type="button"
      disabled
      title={`${label} - coming soon`}
      aria-label={`${label} (coming soon)`}
      className="w-full h-10 short:h-9 flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-900 opacity-60 cursor-not-allowed tiny:justify-center tiny:px-0"
    >
      {icon}
      <span className="flex-1 text-left tiny:hidden">{label}</span>
      <span className="text-[11px] font-medium text-slate-500 tiny:hidden">Coming soon</span>
    </button>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [code, setCode] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await login({ email, password, remember_me: rememberMe });
      if (result.mfa_required && result.mfa_token) {
        setMfaToken(result.mfa_token);
        return;
      }
      if (!result.access_token) {
        setError("Something went wrong signing in.");
        return;
      }
      saveToken(result.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handleMfaSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!mfaToken) return;
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await completeMfaLogin(mfaToken, code);
      saveToken(access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Invalid code");
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogleCredential(credential: string) {
    setError(null);
    try {
      const { access_token, is_new_account } = await googleAuth(credential);
      saveToken(access_token);
      // Symmetric with the signup page - someone who's never signed up
      // before can also land on /login and use Google first, which
      // creates their account here too. New accounts go through plan
      // selection either way.
      router.push(is_new_account ? "/choose-plan" : "/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Google sign-in failed");
    }
  }

  if (mfaToken) {
    return (
      <AuthLayout title="Two-factor verification" subtitle="Enter the 6-digit code from your authenticator app.">
        <form onSubmit={handleMfaSubmit} className="space-y-4">
          <input
            type="text"
            inputMode="numeric"
            autoFocus
            required
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-center text-lg tracking-[0.5em] font-mono placeholder:tracking-normal placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
            placeholder="000000"
          />

          {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

          <button
            type="submit"
            disabled={loading || code.length !== 6}
            className="w-full flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-900 disabled:opacity-60 text-white rounded-lg py-2.5 text-sm font-semibold transition shadow-sm"
          >
            {loading ? "Verifying..." : "Verify"}
          </button>

          <button
            type="button"
            onClick={() => {
              setMfaToken(null);
              setCode("");
              setError(null);
            }}
            className="w-full text-xs text-slate-600 hover:text-slate-700"
          >
            Back to login
          </button>
        </form>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Welcome back!" subtitle="Sign in to your Zoiko Local account.">
      {/* Order matches the design: Microsoft, Google, Apple, SSO. */}
      <div className="grid grid-cols-1 gap-[clamp(0.25rem,0.9vh,0.5rem)] tiny:grid-cols-3">
        <ComingSoonRow {...MICROSOFT} />
        <div className="tiny:order-first tiny:col-span-3">
          <GoogleSignInButton onCredential={handleGoogleCredential} className="h-10 short:h-9" />
        </div>
        <ComingSoonRow {...APPLE} />
        <ComingSoonRow {...SSO} />
      </div>

      <div className="flex items-center gap-3 my-[clamp(0.4rem,1.6vh,1rem)]">
        <div className="h-px bg-slate-200 flex-1" />
        <span className="text-xs text-slate-500">or sign in with email</span>
        <div className="h-px bg-slate-200 flex-1" />
      </div>

      <form onSubmit={handleSubmit} className="space-y-[clamp(0.4rem,1.5vh,0.9rem)]">
        <div>
          <label htmlFor="login-email" className={LABEL}>
            Email address
          </label>
          <div className="relative">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="5" width="18" height="14" rx="2" />
              <path d="M3 7l9 6 9-6" />
            </svg>
            <input
              id="login-email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={INPUT}
              placeholder="name@company.com"
            />
          </div>
        </div>

        <div>
          <label htmlFor="login-password" className={LABEL}>
            Password
          </label>
          <div className="relative">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 z-10" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="4" y="11" width="16" height="10" rx="2" />
              <path d="M8 11V7a4 4 0 018 0v4" />
            </svg>
            <PasswordInput
              id="login-password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={INPUT}
              placeholder="Enter your password"
            />
          </div>
        </div>

        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-[clamp(11px,1.65vh,13px)] text-slate-600 cursor-pointer">
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 accent-slate-800"
            />
            Remember me for 30 days
          </label>
          <Link href="/forgot-password" className="text-[clamp(11px,1.65vh,13px)] font-semibold text-indigo-600 hover:text-indigo-700">
            Forgot password?
          </Link>
        </div>

        {error && (
          <p className="text-[13px] text-red-600 bg-red-50 rounded-lg px-3 py-1.5">{error}</p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full h-[clamp(2.4rem,5.6vh,3.1rem)] flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-900 disabled:opacity-60 text-white rounded-lg text-base font-semibold transition shadow-md shadow-slate-800/25"
        >
          {loading && (
            <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
          )}
          {loading ? "Logging in..." : "Log In"}
        </button>
      </form>

      <p className="text-[13px] text-slate-500 mt-[clamp(0.4rem,1.4vh,0.9rem)] text-center">
        New to Zoiko Local?{" "}
        <Link href="/signup" className="text-indigo-600 font-semibold hover:text-indigo-700">
          Register
        </Link>
      </p>
    </AuthLayout>
  );
}
