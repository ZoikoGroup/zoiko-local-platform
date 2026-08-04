"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  listStaffCases,
  staffApproveCase,
  staffRejectCase,
  ApiError,
  type StaffComplianceCase,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const STATUS_TABS = ["pending", "approved", "rejected", "all"] as const;
type StatusTab = (typeof STATUS_TABS)[number];

export default function StaffCasesPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [tab, setTab] = useState<StatusTab>("pending");
  const [cases, setCases] = useState<StaffComplianceCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const loadCases = useCallback(() => {
    if (!token) return;
    const status = tab === "all" ? undefined : tab;
    return listStaffCases(token, status)
      .then((data) => {
        setCases(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load cases.");
      })
      .finally(() => setLoading(false));
  }, [token, tab, router]);

  useEffect(() => {
    loadCases();
  }, [loadCases]);

  async function handleApprove(caseId: string) {
    if (!token) return;
    setBusyId(caseId);
    try {
      await staffApproveCase(token, caseId);
      await loadCases();
    } catch {
      setError("Couldn't approve this case.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(caseId: string) {
    if (!token) return;
    setBusyId(caseId);
    try {
      await staffRejectCase(token, caseId, rejectReason || undefined);
      setRejectingId(null);
      setRejectReason("");
      await loadCases();
    } catch {
      setError("Couldn't reject this case.");
    } finally {
      setBusyId(null);
    }
  }

  if (!token) return null;

  return (
    <>
      <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1 w-fit">
        {STATUS_TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium capitalize transition ${
              tab === t ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {loading && <p className="text-sm text-slate-500">Loading...</p>}

      {!loading && cases.length === 0 && (
        <p className="text-sm text-slate-500">No {tab === "all" ? "" : tab} cases.</p>
      )}

      <div className="space-y-3">
        {cases.map((c) => (
          <div key={c.id} className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-medium text-white">{c.account_name}</div>
                <div className="text-xs text-slate-500">{c.account_owner_email}</div>
              </div>
              <span
                className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize ${
                  c.status === "pending"
                    ? "bg-amber-950 text-amber-400"
                    : c.status === "approved"
                      ? "bg-emerald-950 text-emerald-400"
                      : "bg-red-950 text-red-400"
                }`}
              >
                {c.status}
              </span>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-slate-500">Jurisdiction</div>
                <div className="text-slate-200">{c.jurisdiction}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Requirement</div>
                <div className="text-slate-200">{c.requirement_type.replaceAll("_", " ")}</div>
              </div>
            </div>

            <div className="mt-3">
              <div className="text-xs text-slate-500 mb-1">Documents submitted</div>
              {c.documents.length === 0 ? (
                <div className="text-sm text-slate-500 italic">None yet</div>
              ) : (
                <ul className="text-sm text-slate-200 space-y-0.5">
                  {c.documents.map((d, i) => (
                    <li key={i}>
                      {d.document_type.replaceAll("_", " ")} —{" "}
                      <span className="text-slate-500 font-mono text-xs">{d.reference}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {c.status === "pending" && (
              <div className="mt-4 pt-4 border-t border-slate-800">
                {rejectingId === c.id ? (
                  <div className="flex gap-2">
                    <input
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                      placeholder="Reason (optional)"
                      className="flex-1 rounded-lg border border-slate-700 bg-slate-800 text-white px-3 py-1.5 text-sm placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-500"
                    />
                    <button
                      onClick={() => handleReject(c.id)}
                      disabled={busyId === c.id}
                      className="text-sm bg-red-700 hover:bg-red-600 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 transition"
                    >
                      Confirm reject
                    </button>
                    <button
                      onClick={() => setRejectingId(null)}
                      className="text-sm text-slate-400 hover:text-slate-200 px-2"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleApprove(c.id)}
                      disabled={busyId === c.id}
                      className="text-sm bg-emerald-700 hover:bg-emerald-600 disabled:opacity-60 text-white rounded-lg px-4 py-1.5 transition"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => setRejectingId(c.id)}
                      className="text-sm bg-slate-800 hover:bg-slate-700 text-white rounded-lg px-4 py-1.5 transition"
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
