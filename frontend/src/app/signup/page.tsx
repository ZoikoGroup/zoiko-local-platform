"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { signup, login, googleAuth, ApiError } from "@/lib/api";
import { saveToken } from "@/lib/auth";
import AuthLayout from "@/components/AuthLayout";
import GoogleSignInButton from "@/components/GoogleSignInButton";
import PasswordInput from "@/components/PasswordInput";

export default function SignupPage() {
  const router = useRouter();
  const [accountName, setAccountName] = useState("");
  const [accountType, setAccountType] = useState<"individual" | "business">(
    "individual"
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await signup({
        account_name: accountName,
        account_type: accountType,
        email,
        password,
      });
      const result = await login({ email, password });
      if (!result.access_token) {
        // Unreachable in practice - a brand new account has no MFA yet -
        // but guarded for type-safety since login() can return null here.
        setError("Something went wrong finishing sign-in after signup.");
        return;
      }
      saveToken(result.access_token);
      // New accounts go through plan selection first, not straight into
      // the product - the login page (returning users) skips this.
      router.push("/choose-plan");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogleCredential(credential: string) {
    setError(null);
    try {
      const { access_token, is_new_account } = await googleAuth(credential);
      saveToken(access_token);
      // An existing customer can land on /signup by mistake (bookmark,
      // stray link) and sign in with Google - that must never route them
      // through plan selection, which really reassigns a real subscription.
      router.push(is_new_account ? "/choose-plan" : "/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Google sign-in failed");
    }
  }

  return (
    <AuthLayout title="Create your account" subtitle="Get started with Zoiko Local.">
      <GoogleSignInButton onCredential={handleGoogleCredential} className="h-10 short:h-9" />

      <div className="flex items-center gap-3 my-[clamp(0.4rem,1.6vh,1rem)]">
        <div className="h-px bg-slate-200 flex-1" />
        <span className="text-xs text-slate-600">or continue with email</span>
        <div className="h-px bg-slate-200 flex-1" />
      </div>

      <form onSubmit={handleSubmit} className="space-y-[clamp(0.35rem,1.3vh,0.9rem)]">
        <div>
          <label className="block text-[clamp(12px,1.75vh,14px)] font-semibold text-slate-800 mb-[0.4vh]">
            Account name
          </label>
          <input
            required
            value={accountName}
            onChange={(e) => setAccountName(e.target.value)}
            className="w-full h-[clamp(2.1rem,4.9vh,2.75rem)] rounded-lg border border-slate-200 px-3.5 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
            placeholder="Your name or company"
          />
        </div>

        <div>
          <label className="block text-[clamp(12px,1.75vh,14px)] font-semibold text-slate-800 mb-[0.4vh]">
            Account type
          </label>
          <div className="grid grid-cols-2 gap-2">
            {(["individual", "business"] as const).map((type) => (
              <button
                type="button"
                key={type}
                onClick={() => setAccountType(type)}
                className={`h-[clamp(2.1rem,4.9vh,2.75rem)] rounded-lg border px-3 text-sm font-medium capitalize transition ${
                  accountType === type
                    ? "border-indigo-600 bg-indigo-50 text-indigo-700 ring-1 ring-indigo-600"
                    : "border-slate-300 text-slate-600 hover:bg-slate-50"
                }`}
              >
                {type}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-[clamp(12px,1.75vh,14px)] font-semibold text-slate-800 mb-[0.4vh]">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full h-[clamp(2.1rem,4.9vh,2.75rem)] rounded-lg border border-slate-200 px-3.5 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
            placeholder="you@example.com"
          />
        </div>

        <div>
          <label className="block text-[clamp(12px,1.75vh,14px)] font-semibold text-slate-800 mb-[0.4vh]">Password</label>
          <PasswordInput
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full h-[clamp(2.1rem,4.9vh,2.75rem)] rounded-lg border border-slate-200 px-3.5 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
            placeholder="At least 8 characters"
          />
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
          {loading ? "Creating account..." : "Create account"}
        </button>
      </form>

      <p className="text-[13px] text-slate-500 mt-[clamp(0.4rem,1.4vh,0.9rem)] text-center">
        Already have an account?{" "}
        <Link href="/login" className="text-indigo-600 font-semibold hover:text-indigo-700">
          Log in
        </Link>
      </p>
    </AuthLayout>
  );
}
