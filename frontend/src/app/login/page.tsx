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

const SOCIAL_PLACEHOLDERS = [
  {
    label: "Continue with Microsoft",
    icon: (
      <svg {...iconProps}>
        <rect x="2" y="2" width="9.5" height="9.5" fill="#f25022" />
        <rect x="12.5" y="2" width="9.5" height="9.5" fill="#7fba00" />
        <rect x="2" y="12.5" width="9.5" height="9.5" fill="#00a4ef" />
        <rect x="12.5" y="12.5" width="9.5" height="9.5" fill="#ffb900" />
      </svg>
    ),
  },
  {
    label: "Continue with Apple",
    icon: (
      <svg {...iconProps} fill="currentColor">
        <path d="M16.4 12.6c0-2.4 2-3.5 2.1-3.6-1.1-1.7-2.9-1.9-3.5-1.9-1.5-.2-2.9.9-3.7.9-.8 0-1.9-.9-3.200-.8-1.600 0-3.100 1-4 2.400-1.700 3-.4 7.400 1.200 9.800.8 1.200 1.800 2.500 3 2.400 1.200 0 1.700-.8 3.100-.8s1.900.8 3.200.8c1.300 0 2.100-1.200 2.900-2.300.9-1.300 1.300-2.600 1.300-2.700-.1 0-2.400-.9-2.400-3.600zM14 5.500c.7-.8 1.100-1.900 1-3-1 0-2.100.7-2.800 1.500-.6.700-1.100 1.800-1 2.900 1.100.1 2.100-.6 2.800-1.400z" />
      </svg>
    ),
  },
  {
    label: "Continue with Company SSO",
    icon: (
      <svg {...iconProps} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <rect x="4" y="3" width="16" height="18" rx="1.500" />
        <path d="M9 21v-4h6v4M8.500 7.500h.01M12 7.500h.01M15.500 7.500h.01M8.500 11.500h.01M12 11.500h.01M15.500 11.500h.01" />
      </svg>
    ),
  },
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await login({ email, password });
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
            className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white rounded-lg py-2.5 text-sm font-medium transition shadow-sm shadow-indigo-600/20"
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
      <div className="space-y-2.5">
        <GoogleSignInButton onCredential={handleGoogleCredential} />
        {/* No backend for these providers yet (only Google is implemented) -
            shown disabled rather than as buttons that do nothing. */}
        {SOCIAL_PLACEHOLDERS.map((p) => (
          <button
            key={p.label}
            type="button"
            disabled
            title="Coming soon"
            className="w-full flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-900 opacity-60 cursor-not-allowed"
          >
            {p.icon}
            <span className="flex-1 text-left">{p.label}</span>
            <span className="text-[11px] font-medium text-slate-500">Coming soon</span>
          </button>
        ))}
      </div>

      <div className="flex items-center gap-3 my-5">
        <div className="h-px bg-slate-200 flex-1" />
        <span className="text-xs text-slate-500">or sign in with email</span>
        <div className="h-px bg-slate-200 flex-1" />
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="login-email" className="block text-sm font-semibold text-slate-800 mb-1.5">
            Email address
          </label>
          <input
            id="login-email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-slate-200 px-3.5 py-3 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
            placeholder="name@company.com"
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label htmlFor="login-password" className="block text-sm font-semibold text-slate-800">
              Password
            </label>
            <Link href="/forgot-password" className="text-xs font-semibold text-indigo-600 hover:text-indigo-700">
              Forgot password?
            </Link>
          </div>
          <PasswordInput
            id="login-password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-slate-200 px-3.5 py-3 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
            placeholder="Enter your password"
          />
        </div>

        {error && (
          <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-900 disabled:opacity-60 text-white rounded-lg py-3.5 text-base font-semibold transition shadow-md shadow-slate-800/25"
        >
          {loading && (
            <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
          )}
          {loading ? "Logging in..." : "Log In"}
        </button>
      </form>

      <p className="text-sm text-slate-500 mt-5 text-center">
        New to Zoiko Local?{" "}
        <Link href="/signup" className="text-indigo-600 font-semibold hover:text-indigo-700">
          Register
        </Link>
      </p>
    </AuthLayout>
  );
}
