"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { getAccessMatrix, ApiError, type AccessMatrixEntry } from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const ROLE_LABELS: Record<string, string> = {
  support: "Support",
  compliance_officer: "Compliance Officer",
  super_admin: "Super Admin",
};

export default function StaffAccessMatrixPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [entries, setEntries] = useState<AccessMatrixEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;
    return getAccessMatrix(token)
      .then((data) => {
        setEntries(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load the access matrix.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  if (!token) return null;

  return (
    <>
      <p className="text-xs text-slate-400">
        Every sensitive staff action is gated by this table, not by role checks scattered across route code - see
        the Commercial Billing Operating Standard doc&apos;s formal RBAC/segregation-of-duties matrix requirement.
        Changing who can do what today means editing seed data, not this page.
      </p>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {!loading && entries.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-400 border-b border-slate-800">
                <th className="pb-2 pr-4 font-medium">Capability</th>
                <th className="pb-2 font-medium">Granted roles</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.capability} className="border-b border-slate-900">
                  <td className="py-2 pr-4 font-mono text-xs text-slate-200">{entry.capability}</td>
                  <td className="py-2 flex flex-wrap gap-1.5">
                    {entry.roles.map((role) => (
                      <span
                        key={role}
                        className="text-xs font-medium rounded-full px-2.5 py-1 bg-slate-800 text-slate-200"
                      >
                        {ROLE_LABELS[role] ?? role}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
