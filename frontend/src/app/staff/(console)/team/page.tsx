"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  listStaffMembers,
  createStaffMember,
  deactivateStaffMember,
  reactivateStaffMember,
  ApiError,
  type StaffMember,
} from "@/lib/api";
import { clearStaffToken, useStaffToken, getOwnStaffIdFromToken } from "@/lib/staffAuth";

const ROLE_LABELS: Record<string, string> = {
  support: "Support (read-only)",
  compliance_officer: "Compliance Officer",
  super_admin: "Super Admin",
};

const ROLES = ["support", "compliance_officer", "super_admin"];

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString();
}

export default function StaffTeamPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const ownStaffId = token ? getOwnStaffIdFromToken(token) : null;

  const [members, setMembers] = useState<StaffMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showAddForm, setShowAddForm] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState(ROLES[0]);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;
    return listStaffMembers(token)
      .then((data) => {
        setMembers(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load the staff list.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!token || !newEmail.trim() || !newPassword) return;
    setCreating(true);
    setError(null);
    try {
      await createStaffMember(token, newEmail.trim(), newPassword, newRole);
      setNewEmail("");
      setNewPassword("");
      setNewRole(ROLES[0]);
      setShowAddForm(false);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "Only staff with the staff.manage_staff capability can add staff."
          : err instanceof ApiError && err.status === 409
          ? "A staff account with that email already exists."
          : "Couldn't add that staff member."
      );
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(member: StaffMember) {
    if (!token) return;
    setBusyId(member.id);
    setError(null);
    try {
      if (member.is_active) {
        await deactivateStaffMember(token, member.id);
      } else {
        await reactivateStaffMember(token, member.id);
      }
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "You can't deactivate your own account."
          : err instanceof ApiError && err.status === 403
          ? "Only staff with the staff.manage_staff capability can do this."
          : "Couldn't update that staff member."
      );
    } finally {
      setBusyId(null);
    }
  }

  if (!token) return null;

  return (
    <>
      <p className="text-xs text-slate-400">
        Everyone with access to this staff console, and at what role. Adding or deactivating someone requires the
        staff.manage_staff capability (Super Admin, by default) - see the Access Matrix for exactly what each role
        can do.
      </p>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      {!showAddForm ? (
        <button
          type="button"
          onClick={() => setShowAddForm(true)}
          className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white"
        >
          + Add staff member
        </button>
      ) : (
        <form
          onSubmit={handleCreate}
          className="flex flex-wrap items-center gap-2 bg-slate-900 border border-slate-800 rounded-lg p-3"
        >
          <input
            type="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            placeholder="email@zoikolocal.com"
            className="flex-1 min-w-[14rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
          />
          <input
            type="text"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="Temporary password"
            className="flex-1 min-w-[12rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
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
            disabled={creating || !newEmail.trim() || !newPassword}
            className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
          >
            {creating ? "Adding..." : "Add"}
          </button>
          <button
            type="button"
            onClick={() => setShowAddForm(false)}
            className="text-xs font-medium rounded-lg px-3 py-1.5 border border-slate-700 text-slate-300 hover:bg-slate-800"
          >
            Cancel
          </button>
        </form>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {!loading && members.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-400 border-b border-slate-800">
                <th className="pb-2 pr-4 font-medium">Email</th>
                <th className="pb-2 pr-4 font-medium">Role</th>
                <th className="pb-2 pr-4 font-medium">Status</th>
                <th className="pb-2 pr-4 font-medium">Added</th>
                <th className="pb-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => {
                const isSelf = m.id === ownStaffId;
                return (
                  <tr key={m.id} className="border-b border-slate-900">
                    <td className="py-2 pr-4 text-slate-200">
                      {m.email}
                      {isSelf && <span className="text-slate-500 ml-1.5 text-xs">(you)</span>}
                    </td>
                    <td className="py-2 pr-4 text-slate-300">{ROLE_LABELS[m.role] ?? m.role}</td>
                    <td className="py-2 pr-4">
                      <span
                        className={`text-[10px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 ${
                          m.is_active ? "bg-emerald-950 text-emerald-300" : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {m.is_active ? "Active" : "Deactivated"}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-slate-400">{formatDate(m.created_at)}</td>
                    <td className="py-2 text-right">
                      {!isSelf && (
                        <button
                          type="button"
                          onClick={() => handleToggleActive(m)}
                          disabled={busyId === m.id}
                          className={`text-xs font-medium rounded-lg px-3 py-1.5 disabled:opacity-50 ${
                            m.is_active
                              ? "border border-red-900 text-red-400 hover:bg-red-950/50"
                              : "bg-indigo-600 hover:bg-indigo-700 text-white"
                          }`}
                        >
                          {busyId === m.id ? "Working..." : m.is_active ? "Deactivate" : "Reactivate"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
