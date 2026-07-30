"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { signup, login, ApiError } from "@/lib/api";
import { saveToken } from "@/lib/auth";

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
      const { access_token } = await login({ email, password });
      saveToken(access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <div className="w-full max-w-sm bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="w-9 h-9 rounded-lg bg-indigo-600 text-white flex items-center justify-center font-bold">
            Z
          </div>
          <div>
            <div className="font-semibold text-slate-900">Zoiko Local</div>
            <div className="text-xs text-slate-500">Communications Platform</div>
          </div>
        </div>

        <h1 className="text-xl font-semibold text-slate-900 mb-1">Create your account</h1>
        <p className="text-sm text-slate-500 mb-6">Get started with Zoiko Local.</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Account name
            </label>
            <input
              required
              value={accountName}
              onChange={(e) => setAccountName(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="Your name or company"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Account type
            </label>
            <div className="flex gap-2">
              {(["individual", "business"] as const).map((type) => (
                <button
                  type="button"
                  key={type}
                  onClick={() => setAccountType(type)}
                  className={`flex-1 rounded-lg border px-3 py-2 text-sm capitalize transition ${
                    accountType === type
                      ? "border-indigo-600 bg-indigo-50 text-indigo-700"
                      : "border-slate-300 text-slate-600"
                  }`}
                >
                  {type}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Password</label>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="At least 8 characters"
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white rounded-lg py-2 text-sm font-medium transition"
          >
            {loading ? "Creating account..." : "Create account"}
          </button>
        </form>

        <p className="text-sm text-slate-500 mt-6 text-center">
          Already have an account?{" "}
          <Link href="/login" className="text-indigo-600 font-medium">
            Log in
          </Link>
        </p>
      </div>
    </div>
  );
}
