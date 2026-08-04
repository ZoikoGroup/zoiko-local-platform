"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  listStaffAccounts,
  searchStaffNumbers,
  ApiError,
  type AccountOverview,
  type StaffNumberSearchResult,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

export default function StaffAccountsPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [accounts, setAccounts] = useState<AccountOverview[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [numberQuery, setNumberQuery] = useState("");
  const [numberResults, setNumberResults] = useState<StaffNumberSearchResult[] | null>(null);
  const [numberSearching, setNumberSearching] = useState(false);
  const [numberSearchError, setNumberSearchError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const loadAccounts = useCallback(() => {
    if (!token) return;
    return listStaffAccounts(token)
      .then((data) => {
        setAccounts(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load accounts.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  async function handleNumberSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !numberQuery.trim()) return;
    setNumberSearching(true);
    setNumberSearchError(null);
    try {
      setNumberResults(await searchStaffNumbers(token, numberQuery.trim()));
    } catch (err) {
      setNumberSearchError(err instanceof ApiError ? err.message : "Search failed.");
    } finally {
      setNumberSearching(false);
    }
  }

  if (!token) return null;

  return (
    <>
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
        <div>
          <h3 className="font-medium text-white">Find a Number</h3>
          <p className="text-xs text-slate-500">
            Search by phone number or provider SID, across every account.
          </p>
        </div>
        <form onSubmit={handleNumberSearch} className="flex gap-2">
          <input
            value={numberQuery}
            onChange={(e) => setNumberQuery(e.target.value)}
            placeholder="+1555... or PN..."
            className="flex-1 rounded-lg border border-slate-700 bg-slate-800 text-white px-3 py-1.5 text-sm font-mono placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-500"
          />
          <button
            type="submit"
            disabled={numberSearching || !numberQuery.trim()}
            className="text-sm bg-slate-700 hover:bg-slate-600 disabled:opacity-60 text-white rounded-lg px-4 py-1.5 transition"
          >
            {numberSearching ? "Searching..." : "Search"}
          </button>
        </form>

        {numberSearchError && <p className="text-sm text-red-400">{numberSearchError}</p>}

        {numberResults !== null && (
          <>
            {numberResults.length === 0 ? (
              <p className="text-sm text-slate-500">No matching numbers.</p>
            ) : (
              <div className="space-y-2">
                {numberResults.map((r) => (
                  <div
                    key={r.id}
                    className="flex items-center justify-between rounded-lg border border-slate-800 px-3 py-2"
                  >
                    <div>
                      <span className="font-mono text-white">{r.e164}</span>
                      <span className="ml-3 text-xs text-slate-500">
                        {r.account_name ?? "Unknown account"} &middot; {r.account_owner_email ?? "—"}
                      </span>
                    </div>
                    <span className="text-xs font-medium text-slate-400 capitalize">
                      {r.status.replaceAll("_", " ")}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {loading && <p className="text-sm text-slate-500">Loading...</p>}

      {!loading && accounts.length === 0 && (
        <p className="text-sm text-slate-500">No accounts yet.</p>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
              <th className="px-4 py-2.5 font-medium">Account</th>
              <th className="px-4 py-2.5 font-medium">Owner</th>
              <th className="px-4 py-2.5 font-medium">Type</th>
              <th className="px-4 py-2.5 font-medium">Members</th>
              <th className="px-4 py-2.5 font-medium">Numbers</th>
              <th className="px-4 py-2.5 font-medium">Created</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <tr key={a.id} className="border-b border-slate-800/60 last:border-0">
                <td className="px-4 py-2.5 text-white">{a.name}</td>
                <td className="px-4 py-2.5 text-slate-400">{a.owner_email ?? "—"}</td>
                <td className="px-4 py-2.5 text-slate-400 capitalize">{a.account_type}</td>
                <td className="px-4 py-2.5 text-slate-200">{a.member_count}</td>
                <td className="px-4 py-2.5 text-slate-200">{a.number_count}</td>
                <td className="px-4 py-2.5 text-slate-500">
                  {new Date(a.created_at).toLocaleDateString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
