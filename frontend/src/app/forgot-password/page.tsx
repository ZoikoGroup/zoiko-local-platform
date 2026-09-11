"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { forgotPassword, ApiError } from "@/lib/api";
import AuthLayout from "@/components/AuthLayout";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (err) {
      // Never reveal whether the email matched an account (same posture
      // as the backend's always-204 response) - a generic message here
      // even on a real request failure avoids leaking anything useful.
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  if (sent) {
    return (
      <AuthLayout
        title="Check your email"
        subtitle={`If an account exists for ${email}, we've sent a link to reset your password. It expires in 30 minutes.`}
      >
        <p className="text-sm text-slate-500 text-center">
          <Link href="/login" className="text-indigo-600 font-medium hover:text-indigo-700">
            Back to login
          </Link>
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Forgot your password?" subtitle="Enter your email and we'll send you a reset link.">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">Email</label>
          <input
            type="email"
            required
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 transition"
            placeholder="you@example.com"
          />
        </div>

        {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

        <button
          type="submit"
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white rounded-lg py-2.5 text-sm font-medium transition shadow-sm shadow-indigo-600/20"
        >
          {loading ? "Sending..." : "Send reset link"}
        </button>

        <p className="text-sm text-slate-500 text-center">
          Remembered it?{" "}
          <Link href="/login" className="text-indigo-600 font-medium hover:text-indigo-700">
            Back to login
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}
