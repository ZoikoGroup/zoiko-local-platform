"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  getAccessMatrix,
  grantCapability,
  revokeCapability,
  ApiError,
  type AccessMatrixEntry,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const ROLE_LABELS: Record<string, string> = {
  support: "Support",
  compliance_officer: "Compliance Officer",
  super_admin: "Super Admin",
};

const ROLES = ["support", "compliance_officer", "super_admin"];

export default function StaffAccessMatrixPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [entries, setEntries] = useState<AccessMatrixEntry[]>([]);
  const [loading, setLoading] = useState(true );
  const [error, setError] = useState<string | null>(null);

  const [newCapability, setNewCapability] = useState("");
  const [newRole, setNewRole] = useState(ROLES[0]);
  const [granting, setGranting] = useState(false);
  const [revokingKey, setRevokingKey] = useState<string | null>(null);

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

  async function handleGrant(e: FormEvent) {
    e.preventDefault();
    if (!token || !newCapability.trim()) return;
    setGranting(true);
    setError(null);
    try {
      await grantCapability(token, newCapability.trim(), newRole);
      setNewCapability("");
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "Only staff with the staff.manage_capabilities capability can edit this matrix."
          : "Couldn't grant that capability."
      );
    } finally {
      setGranting(false);
    }
  }

  async function handleRevoke(capability: string, role: string) {
    if (!token) return;
    const key = `${capability}:${role}`;
    setRevokingKey(key);
    setError(null);
    try {
      await revokeCapability(token, capability, role);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Can't revoke the last role able to manage this matrix - grant another role first."
          : err instanceof ApiError && err.status === 403
          ? "Only staff with the staff.manage_capabilities capability can edit this matrix."
          : "Couldn't revoke that role."
      );
    } finally {
      setRevokingKey(null);
    }
  }

  if (!token) return null;

  return (
    <>
      <p className="text-xs text-slate-400">
        Every sensitive staff action is gated by this table, not by role checks scattered across route code - see
        the Commercial Billing Operating Standard doc&apos;s formal RBAC/segregation-of-duties matrix requirement.
        Granting or revoking a role below requires the staff.manage_capabilities capability itself (Super Admin
        only, by default).
      </p>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      <form onSubmit={handleGrant} className="flex flex-wrap items-center gap-2 bg-slate-900 border border-slate-800 rounded-lg p-3">
        <input
          value={newCapability}
          onChange={(e) => setNewCapability(e.target.value)}
          placeholder="e.g. risk.manage_blocked_destinations"
          className="flex-1 min-w-[16rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 font-mono placeholder:text-slate-500"
        />
        <select
          value={newRole}
          onChange={(e) => setNewRole(e.target.value)}
          className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5"
        >
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {ROLE_LABELS[role]}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={granting || !newCapability.trim()}
          className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
        >
          {granting ? "Granting..." : "Grant"}
        </button>
      </form>

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
                  <td className="py-2 pr-4 font-mono text-xs text-slate-200 align-top">{entry.capability}</td>
                  <td className="py-2 flex flex-wrap gap-1.5">
                    {entry.roles.map((role) => {
                      const key = `${entry.capability}:${role}`;
                      return (
                        <span
                          key={role}
                          className="inline-flex items-center gap-1.5 text-xs font-medium rounded-full pl-2.5 pr-1.5 py-1 bg-slate-800 text-slate-200"
                        >
                          {ROLE_LABELS[role] ?? role}
                          <button
                            type="button"
                            onClick={() => handleRevoke(entry.capability, role)}
                            disabled={revokingKey === key}
                            title="Revoke"
                            className="text-slate-500 hover:text-red-400 disabled:opacity-50 leading-none"
                          >
                            &times;
                          </button>
                        </span>
                      );
                    })}
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
