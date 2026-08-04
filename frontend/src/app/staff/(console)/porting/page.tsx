"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  listStaffPortingRequests,
  staffApprovePortingRequest,
  staffRejectPortingRequest,
  staffCompletePortingRequest,
  ApiError,
  type StaffPortingRequest,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const STATUS_TABS = ["submitted", "approved", "completed", "rejected", "all"] as const;
type StatusTab = (typeof STATUS_TABS)[number];

export default function StaffPortingPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [tab, setTab] = useState<StatusTab>("submitted");
  const [requests, setRequests] = useState<StaffPortingRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [completingId, setCompletingId] = useState<string | null>(null);
  const [completeSid, setCompleteSid] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const loadRequests = useCallback(() => {
    if (!token) return;
    const status = tab === "all" ? undefined : tab;
    return listStaffPortingRequests(token, status)
      .then((data) => {
        setRequests(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load porting requests.");
      })
      .finally(() => setLoading(false));
  }, [token, tab, router]);

  useEffect(() => {
    loadRequests();
  }, [loadRequests]);

  async function handleApprove(requestId: string) {
    if (!token) return;
    setBusyId(requestId);
    try {
      await staffApprovePortingRequest(token, requestId);
      await loadRequests();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't approve this request.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(requestId: string) {
    if (!token) return;
    setBusyId(requestId);
    try {
      await staffRejectPortingRequest(token, requestId, rejectReason || undefined);
      setRejectingId(null);
      setRejectReason("");
      await loadRequests();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reject this request.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleComplete(requestId: string) {
    if (!token || !completeSid.trim()) return;
    setBusyId(requestId);
    try {
      await staffCompletePortingRequest(token, requestId, completeSid.trim());
      setCompletingId(null);
      setCompleteSid("");
      await loadRequests();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't complete this request.");
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

      <p className="text-xs text-slate-500 bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">
        Approving vets the request only - the actual port with the losing carrier is performed by hand
        (LOA, carrier coordination), then marked Complete here once the number is live on Twilio.
      </p>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {loading && <p className="text-sm text-slate-500">Loading...</p>}

      {!loading && requests.length === 0 && (
        <p className="text-sm text-slate-500">No {tab === "all" ? "" : tab} porting requests.</p>
      )}

      <div className="space-y-3">
        {requests.map((r) => (
          <div key={r.id} className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-mono font-medium text-white">{r.phone_number}</div>
                <div className="text-xs text-slate-500">
                  {r.account_name} &middot; {r.account_owner_email}
                </div>
              </div>
              <span
                className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize ${
                  r.status === "submitted"
                    ? "bg-amber-950 text-amber-400"
                    : r.status === "approved"
                      ? "bg-indigo-950 text-indigo-400"
                      : r.status === "completed"
                        ? "bg-emerald-950 text-emerald-400"
                        : r.status === "rejected"
                          ? "bg-red-950 text-red-400"
                          : "bg-slate-800 text-slate-400"
                }`}
              >
                {r.status}
              </span>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-slate-500">Country</div>
                <div className="text-slate-200">{r.country}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Current carrier</div>
                <div className="text-slate-200">{r.current_carrier}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Carrier account #</div>
                <div className="text-slate-200 font-mono">{r.carrier_account_number}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Billing name</div>
                <div className="text-slate-200">{r.billing_name}</div>
              </div>
              <div className="col-span-2">
                <div className="text-xs text-slate-500">Billing address</div>
                <div className="text-slate-200">{r.billing_address}</div>
              </div>
            </div>

            {r.status === "rejected" && r.rejection_reason && (
              <p className="mt-3 text-sm text-red-400">Reason: {r.rejection_reason}</p>
            )}
            {r.status === "completed" && r.twilio_incoming_number_sid && (
              <p className="mt-3 text-xs text-slate-500 font-mono">SID: {r.twilio_incoming_number_sid}</p>
            )}

            {r.status === "submitted" && (
              <div className="mt-4 pt-4 border-t border-slate-800">
                {rejectingId === r.id ? (
                  <div className="flex gap-2">
                    <input
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                      placeholder="Reason (optional)"
                      className="flex-1 rounded-lg border border-slate-700 bg-slate-800 text-white px-3 py-1.5 text-sm placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-500"
                    />
                    <button
                      onClick={() => handleReject(r.id)}
                      disabled={busyId === r.id}
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
                      onClick={() => handleApprove(r.id)}
                      disabled={busyId === r.id}
                      className="text-sm bg-emerald-700 hover:bg-emerald-600 disabled:opacity-60 text-white rounded-lg px-4 py-1.5 transition"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => setRejectingId(r.id)}
                      className="text-sm bg-slate-800 hover:bg-slate-700 text-white rounded-lg px-4 py-1.5 transition"
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
            )}

            {r.status === "approved" && (
              <div className="mt-4 pt-4 border-t border-slate-800">
                {completingId === r.id ? (
                  <div className="flex gap-2">
                    <input
                      value={completeSid}
                      onChange={(e) => setCompleteSid(e.target.value)}
                      placeholder="Twilio incoming number SID (PN...)"
                      className="flex-1 rounded-lg border border-slate-700 bg-slate-800 text-white px-3 py-1.5 text-sm font-mono placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-500"
                    />
                    <button
                      onClick={() => handleComplete(r.id)}
                      disabled={busyId === r.id || !completeSid.trim()}
                      className="text-sm bg-emerald-700 hover:bg-emerald-600 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 transition"
                    >
                      Confirm complete
                    </button>
                    <button
                      onClick={() => {
                        setCompletingId(null);
                        setCompleteSid("");
                      }}
                      className="text-sm text-slate-400 hover:text-slate-200 px-2"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setCompletingId(r.id)}
                    className="text-sm bg-emerald-700 hover:bg-emerald-600 text-white rounded-lg px-4 py-1.5 transition"
                  >
                    Mark port complete
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
