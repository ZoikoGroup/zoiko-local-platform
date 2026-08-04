"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { listStaffAccounts, ApiError, type AccountOverview } from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

export default function StaffAccountsPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [accounts, setAccounts] = useState<AccountOverview[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  if (!token) return null;

  return (
    <>
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
