"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  listStaffCountries,
  upsertStaffCountry,
  setCountryMarketStatus,
  setCountryCapabilities,
  listCountryApprovals,
  recordCountryApproval,
  COUNTRY_MARKET_STATUSES,
  COUNTRY_APPROVAL_TYPES,
  ApiError,
  type StaffCountry,
  type CountryApprovalRecord,
  type CountryApprovalTypeName,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";
import { useStaffRole } from "@/lib/staffRole";

const STATUS_LABELS: Record<string, string> = {
  closed: "Closed",
  launching: "Launching",
  open: "Open",
  restricted: "Restricted",
  suspended: "Suspended",
};

const STATUS_COLORS: Record<string, string> = {
  closed: "bg-slate-800 text-slate-400",
  launching: "bg-amber-900 text-amber-200",
  open: "bg-emerald-900 text-emerald-200",
  restricted: "bg-orange-900 text-orange-200",
  suspended: "bg-red-900 text-red-200",
};

const APPROVAL_LABELS: Record<CountryApprovalTypeName, string> = {
  regulatory: "Regulatory / Compliance",
  finance: "Finance / Tax",
  commercial: "Commercial Launch",
};

const APPROVAL_CAPABILITY: Record<CountryApprovalTypeName, string> = {
  regulatory: "numbers.approve_country_regulatory",
  finance: "numbers.approve_country_finance",
  commercial: "numbers.approve_country_commercial",
};

const CAPABILITY_FIELDS: { key: keyof StaffCountry; label: string }[] = [
  { key: "customer_signup_enabled", label: "Customer signup" },
  { key: "number_search_enabled", label: "Number search" },
  { key: "number_purchase_enabled", label: "Number purchase" },
  { key: "inbound_voice_enabled", label: "Inbound voice" },
  { key: "outbound_voice_enabled", label: "Outbound voice" },
  { key: "sms_enabled", label: "SMS" },
  { key: "porting_supported", label: "Porting" },
  { key: "recording_enabled", label: "Recording" },
];

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export default function StaffCountriesPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const { capabilities } = useStaffRole();
  const canManageList = capabilities.has("numbers.manage_country_list");

  const [countries, setCountries] = useState<StaffCountry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<Record<string, CountryApprovalRecord[]>>({});

  const [newCode, setNewCode] = useState("");
  const [newName, setNewName] = useState("");
  const [addingCountry, setAddingCountry] = useState(false);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  const load = useCallback(() => {
    if (!token) return;
    return listStaffCountries(token)
      .then((data) => {
        setCountries(data);
        setError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        setError("Couldn't load countries.");
      })
      .finally(() => setLoading(false));
  }, [token, router]);

  useEffect(() => {
    load();
  }, [load]);

  function loadApprovals(code: string) {
    if (!token) return;
    listCountryApprovals(token, code).then((data) => {
      setApprovals((prev) => ({ ...prev, [code]: data }));
    });
  }

  function toggleExpanded(code: string) {
    const next = expanded === code ? null : code;
    setExpanded(next);
    if (next) loadApprovals(next);
  }

  async function handleAddCountry() {
    if (!token || !newCode.trim() || !newName.trim()) return;
    setAddingCountry(true);
    setError(null);
    try {
      await upsertStaffCountry(token, newCode.trim().toUpperCase(), newName.trim());
      setNewCode("");
      setNewName("");
      await load();
    } catch {
      setError("Couldn't add that country.");
    } finally {
      setAddingCountry(false);
    }
  }

  if (!token) return null;

  return (
    <>
      <p className="text-xs text-slate-400">
        Global Country Commercial Activation Directive (ZL-COM-LAUNCH-001) - each country moves through{" "}
        <em>Closed → Launching → Open/Restricted → Suspended</em>, with 8 independent capability switches and 3
        separately-owned approvals (Regulatory, Finance, Commercial) required before it can reach Open. Reaching
        Open does not itself enable any capability - each one is turned on individually once its own review clears.
      </p>

      {error && <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">{error}</p>}

      {canManageList && (
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-[11px] text-slate-400 mb-1">Country code</label>
            <input
              value={newCode}
              onChange={(e) => setNewCode(e.target.value)}
              placeholder="e.g. BR"
              maxLength={2}
              className="w-20 text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 uppercase"
            />
          </div>
          <div>
            <label className="block text-[11px] text-slate-400 mb-1">Name</label>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Brazil"
              className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5"
            />
          </div>
          <button
            type="button"
            onClick={handleAddCountry}
            disabled={addingCountry || !newCode.trim() || !newName.trim()}
            className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
          >
            {addingCountry ? "Adding..." : "Add country"}
          </button>
        </div>
      )}

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {!loading && (
        <div className="space-y-2">
          {countries.map((country) => (
            <div key={country.code} className="rounded-lg border border-slate-800 bg-slate-900 overflow-hidden">
              <button
                type="button"
                onClick={() => toggleExpanded(country.code)}
                className="w-full flex items-center justify-between gap-4 px-4 py-3 text-left hover:bg-slate-800/50"
              >
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-slate-500 w-8">{country.code}</span>
                  <span className="text-sm font-medium text-slate-100">{country.name}</span>
                </div>
                <span
                  className={`text-[10px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 ${
                    STATUS_COLORS[country.market_status] ?? "bg-slate-800 text-slate-400"
                  }`}
                >
                  {STATUS_LABELS[country.market_status] ?? country.market_status}
                </span>
              </button>

              {expanded === country.code && (
                <CountryDetail
                  country={country}
                  token={token}
                  canManageList={canManageList}
                  capabilities={capabilities}
                  approvals={approvals[country.code] ?? []}
                  onChanged={() => {
                    load();
                    loadApprovals(country.code);
                  }}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function CountryDetail({
  country, token, canManageList, capabilities, approvals, onChanged,
}: {
  country: StaffCountry;
  token: string;
  canManageList: boolean;
  capabilities: Set<string>;
  approvals: CountryApprovalRecord[];
  onChanged: () => void;
}) {
  const [statusReason, setStatusReason] = useState("");
  const [pendingStatus, setPendingStatus] = useState(country.market_status);
  const [statusBusy, setStatusBusy] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [flags, setFlags] = useState<Record<string, boolean>>(
    Object.fromEntries(CAPABILITY_FIELDS.map((f) => [f.key, Boolean(country[f.key])]))
  );
  const [flagsReason, setFlagsReason] = useState("");
  const [flagsBusy, setFlagsBusy] = useState(false);
  const [flagsError, setFlagsError] = useState<string | null>(null);

  async function handleStatusUpdate() {
    if (!statusReason.trim()) {
      setStatusError("A reason is required.");
      return;
    }
    setStatusBusy(true);
    setStatusError(null);
    try {
      await setCountryMarketStatus(token, country.code, pendingStatus, statusReason.trim());
      setStatusReason("");
      onChanged();
    } catch (err) {
      setStatusError(err instanceof ApiError ? err.message : "Couldn't update the status.");
    } finally {
      setStatusBusy(false);
    }
  }

  async function handleFlagsSave() {
    if (!flagsReason.trim()) {
      setFlagsError("A reason is required.");
      return;
    }
    setFlagsBusy(true);
    setFlagsError(null);
    try {
      await setCountryCapabilities(token, country.code, flagsReason.trim(), flags);
      setFlagsReason("");
      onChanged();
    } catch {
      setFlagsError("Couldn't save capabilities.");
    } finally {
      setFlagsBusy(false);
    }
  }

  return (
    <div className="border-t border-slate-800 p-4 space-y-4">
      {/* Market status */}
      <div>
        <div className="text-xs font-semibold text-slate-300 mb-2">Market status</div>
        {canManageList ? (
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={pendingStatus}
              onChange={(e) => setPendingStatus(e.target.value)}
              className="text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5"
            >
              {COUNTRY_MARKET_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABELS[s]}
                </option>
              ))}
            </select>
            <input
              value={statusReason}
              onChange={(e) => setStatusReason(e.target.value)}
              placeholder="Reason (required)"
              className="flex-1 min-w-[14rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
            />
            <button
              type="button"
              onClick={handleStatusUpdate}
              disabled={statusBusy}
              className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
            >
              {statusBusy ? "Updating..." : "Update status"}
            </button>
          </div>
        ) : (
          <p className="text-xs text-slate-500">You don&apos;t have the numbers.manage_country_list capability.</p>
        )}
        {statusError && <p className="text-xs text-red-400 mt-2">{statusError}</p>}
      </div>

      {/* Capability flags */}
      <div>
        <div className="text-xs font-semibold text-slate-300 mb-2">Capability flags (independent per-service switches)</div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {CAPABILITY_FIELDS.map((f) => (
            <label key={f.key} className="flex items-center gap-2 text-xs text-slate-300">
              <input
                type="checkbox"
                checked={Boolean(flags[f.key])}
                disabled={!canManageList}
                onChange={(e) => setFlags((prev) => ({ ...prev, [f.key]: e.target.checked }))}
              />
              {f.label}
            </label>
          ))}
        </div>
        {canManageList && (
          <div className="flex flex-wrap items-center gap-2 mt-3">
            <input
              value={flagsReason}
              onChange={(e) => setFlagsReason(e.target.value)}
              placeholder="Reason (required)"
              className="flex-1 min-w-[14rem] text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500"
            />
            <button
              type="button"
              onClick={handleFlagsSave}
              disabled={flagsBusy}
              className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white"
            >
              {flagsBusy ? "Saving..." : "Save capabilities"}
            </button>
          </div>
        )}
        {flagsError && <p className="text-xs text-red-400 mt-2">{flagsError}</p>}
      </div>

      {/* Approvals */}
      <div>
        <div className="text-xs font-semibold text-slate-300 mb-2">
          Launch approvals - all 3 required before Open
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {COUNTRY_APPROVAL_TYPES.map((approvalType) => {
            const record = approvals.find((a) => a.approval_type === approvalType);
            const canApprove = capabilities.has(APPROVAL_CAPABILITY[approvalType]);
            return (
              <ApprovalCard
                key={approvalType}
                approvalType={approvalType}
                record={record}
                canApprove={canApprove}
                token={token}
                code={country.code}
                onChanged={onChanged}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ApprovalCard({
  approvalType, record, canApprove, token, code, onChanged,
}: {
  approvalType: CountryApprovalTypeName;
  record: CountryApprovalRecord | undefined;
  canApprove: boolean;
  token: string;
  code: string;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [ownerName, setOwnerName] = useState("");
  const [ownerTitle, setOwnerTitle] = useState("");
  const [evidenceReference, setEvidenceReference] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const status = record?.status ?? "pending";

  async function submit(newStatus: "approved" | "rejected") {
    if (!ownerName.trim() || !ownerTitle.trim() || !evidenceReference.trim() || !reason.trim()) {
      setFormError("All fields are required.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      await recordCountryApproval(token, code, approvalType, {
        status: newStatus, owner_name: ownerName.trim(), owner_title: ownerTitle.trim(),
        evidence_reference: evidenceReference.trim(), reason: reason.trim(),
      });
      setEditing(false);
      setOwnerName("");
      setOwnerTitle("");
      setEvidenceReference("");
      setReason("");
      onChanged();
    } catch (err) {
      setFormError(
        err instanceof ApiError && err.status === 403
          ? "You don't have the matching approval capability."
          : "Couldn't record the approval."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`rounded-lg border p-3 ${
        status === "approved"
          ? "border-emerald-900 bg-emerald-950/30"
          : status === "rejected"
          ? "border-red-900 bg-red-950/30"
          : "border-slate-800 bg-slate-950/40"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-200">{APPROVAL_LABELS[approvalType]}</span>
        <span
          className={`text-[10px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 ${
            status === "approved"
              ? "bg-emerald-900 text-emerald-200"
              : status === "rejected"
              ? "bg-red-900 text-red-200"
              : "bg-slate-800 text-slate-400"
          }`}
        >
          {status}
        </span>
      </div>
      {record?.owner_name && (
        <div className="text-[11px] text-slate-500 mt-1 space-y-0.5">
          <div>{record.owner_name}{record.owner_title ? ` — ${record.owner_title}` : ""}</div>
          {record.evidence_reference && <div>Ref: {record.evidence_reference}</div>}
          <div>Decided: {formatDate(record.decided_at)}</div>
        </div>
      )}

      {canApprove && !editing && (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="mt-2 text-[11px] font-medium rounded-lg px-2.5 py-1 border border-slate-700 text-slate-300 hover:bg-slate-800"
        >
          Record decision
        </button>
      )}

      {canApprove && editing && (
        <div className="mt-2 space-y-1.5">
          <input
            value={ownerName}
            onChange={(e) => setOwnerName(e.target.value)}
            placeholder="Owner name"
            className="w-full text-xs rounded-lg bg-slate-800 border border-slate-700 text-white px-2 py-1 placeholder:text-slate-500"
          />
          <input
            value={ownerTitle}
            onChange={(e) => setOwnerTitle(e.target.value)}
            placeholder="Owner title"
            className="w-full text-xs rounded-lg bg-slate-800 border border-slate-700 text-white px-2 py-1 placeholder:text-slate-500"
          />
          <input
            value={evidenceReference}
            onChange={(e) => setEvidenceReference(e.target.value)}
            placeholder="Evidence reference"
            className="w-full text-xs rounded-lg bg-slate-800 border border-slate-700 text-white px-2 py-1 placeholder:text-slate-500"
          />
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason"
            rows={2}
            className="w-full text-xs rounded-lg bg-slate-800 border border-slate-700 text-white px-2 py-1 placeholder:text-slate-500"
          />
          {formError && <p className="text-[11px] text-red-400">{formError}</p>}
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={() => submit("approved")}
              disabled={busy}
              className="text-[11px] font-medium rounded-lg px-2.5 py-1 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white"
            >
              Approve
            </button>
            <button
              type="button"
              onClick={() => submit("rejected")}
              disabled={busy}
              className="text-[11px] font-medium rounded-lg px-2.5 py-1 bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white"
            >
              Reject
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-[11px] font-medium rounded-lg px-2.5 py-1 border border-slate-700 text-slate-300 hover:bg-slate-800"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
