"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  listStaffTeam,
  createStaffTeamMember,
  deactivateStaffTeamMember,
  reactivateStaffTeamMember,
  getCurrentStaff,
  ApiError,
  type StaffTeamMember,
  type StaffRole,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const ROLE_LABELS: Record<StaffRole, string> = {
  support: "Support",
  compliance_officer: "Compliance Officer",
  super_admin: "Super Admin",
};

const ROLES: StaffRole[] = ["support", "compliance_officer", "super_admin"];

export default function StaffTeamPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [members, setMembers] = useState<StaffTeamMember[]>([]);
  const [selfId, setSelfId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<StaffRole>("support");
  const [creating, setCreating] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;
    return Promise.all([listStaffTeam(token), getCurrentStaff(token)])
      .then(([team, me]) => {
        setMembers(team);
        setSelfId(me.id);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load the staff team.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!token || !email.trim() || password.length < 8) return;
    setCreating(true);
    setError(null);
    try {
      await createStaffTeamMember(token, { email: email.trim(), password, role });
      setEmail("");
      setPassword("");
      setRole("support");
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("A staff account with that email already exists.");
      } else if (err instanceof ApiError && err.status === 403) {
        setError("Only staff with the staff.manage_staff_accounts capability can add a teammate.");
      } else if (err instanceof ApiError && err.status === 422) {
        setError("Password must be at least 8 characters.");
      } else {
        setError("Couldn't create that staff account.");
      }
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(member: StaffTeamMember) {
    if (!token) return;
    setTogglingId(member.id);
    setError(null);
    try {
      if (member.is_active) {
        await deactivateStaffTeamMember(token, member.id);
      } else {
        await reactivateStaffTeamMember(token, member.id);
      }
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("Can't deactivate the only active Super Admin - promote another account first.");
      } else if (err instanceof ApiError && err.status === 403) {
        setError("Only staff with the staff.manage_staff_accounts capability can change this.");
      } else {
        setError("Couldn't update that account.");
      }
    } finally {
      setTogglingId(null);
    }
  }

  if (!token) return null;

  return (
    <>
      <p className="text-xs text-slate-400">
        Every staff account with console access, and their role in the capability matrix (see Access Matrix for
        what each role can actually do). Creating or deactivating an account requires the
        staff.manage_staff_accounts capability (Super Admin only, by default). Deactivating someone takes effect on
        their very next request — an existing session doesn&apos;t linger.
      </p>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>
      )}

      <form
        onSubmit={handleCreate}
        className="flex flex-wrap items-center gap-2 bg-slate-900 border border-slate-800 rounded-lg p-3"
      >
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@zoikogroup.com"
          className="flex-1 min-w-[14rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password (min 8 characters)"
          className="flex-1 min-w-[14rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
        />
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as StaffRole)}
          className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={creating || !email.trim() || password.length < 8}
          className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
        >
          {creating ? "Adding..." : "Add teammate"}
        </button>
      </form>

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {!loading && members.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-left text-xs text-slate-400">
                <th className="px-4 py-2.5 font-medium">Email</th>
                <th className="px-4 py-2.5 font-medium">Role</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Added</th>
                <th className="px-4 py-2.5 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id} className="border-b border-slate-800/60 last:border-0">
                  <td className="px-4 py-2.5 text-white">
                    {m.email}
                    {m.id === selfId && <span className="ml-2 text-xs text-slate-500">(you)</span>}
                  </td>
                  <td className="px-4 py-2.5 text-slate-300">{ROLE_LABELS[m.role] ?? m.role}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`text-xs font-medium rounded-full px-2.5 py-1 ${
                        m.is_active ? "bg-emerald-950 text-emerald-400" : "bg-slate-800 text-slate-400"
                      }`}
                    >
                      {m.is_active ? "Active" : "Deactivated"}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-slate-400">{new Date(m.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      type="button"
                      onClick={() => handleToggleActive(m)}
                      disabled={togglingId === m.id || m.id === selfId}
                      title={m.id === selfId ? "You can't deactivate your own account" : undefined}
                      className={`text-xs font-medium rounded-lg px-2.5 py-1 disabled:opacity-40 ${
                        m.is_active
                          ? "bg-slate-800 hover:bg-red-950 hover:text-red-400 text-slate-300"
                          : "bg-slate-800 hover:bg-emerald-950 hover:text-emerald-400 text-slate-300"
                      }`}
                    >
                      {togglingId === m.id ? "..." : m.is_active ? "Deactivate" : "Reactivate"}
                    </button>
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
