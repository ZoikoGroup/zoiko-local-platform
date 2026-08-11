"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  listFraudCases,
  resolveFraudCase,
  listFraudRules,
  upsertFraudRule,
  ApiError,
  type FraudCase,
  type FraudCaseStatus,
  type FraudRule,
  type RiskSignalType,
} from "@/lib/api";
import { clearStaffToken, useStaffToken } from "@/lib/staffAuth";

const CASE_STATUS_TABS = ["open", "confirmed", "cleared", "all"] as const;
type CaseStatusTab = (typeof CASE_STATUS_TABS)[number];

// Mirrors risk/service.py's _DEFAULT_WEIGHTS - shown for a signal type even
// before staff have ever saved an override row for it, so the rules table
// always covers every signal the fraud model can fire, not just the ones
// someone has already tuned.
const KNOWN_SIGNAL_TYPES: { type: RiskSignalType; label: string; defaultWeight: number }[] = [
  { type: "blocked_destination_attempt", label: "Blocked destination attempt", defaultWeight: 40 },
  { type: "velocity_exceeded", label: "Outbound velocity exceeded", defaultWeight: 30 },
  { type: "geographic_dispersion", label: "Geographic dispersion (IRSF)", defaultWeight: 25 },
];

const SECTIONS = ["cases", "rules"] as const;
type Section = (typeof SECTIONS)[number];

export default function StaffFraudPage() {
  const router = useRouter();
  const { token, ready } = useStaffToken();
  const [section, setSection] = useState<Section>("cases");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !token) router.replace("/staff/login");
  }, [ready, token, router]);

  if (!token) return null;

  return (
    <>
      <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1 w-fit">
        {SECTIONS.map((s) => (
          <button
            key={s}
            onClick={() => setSection(s)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium capitalize transition ${
              section === s ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {s === "cases" ? "Review Queue" : "Scoring Rules"}
          </button>
        ))}
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {section === "cases" ? (
        <FraudCasesSection token={token} onError={setError} />
      ) : (
        <FraudRulesSection token={token} onError={setError} />
      )}
    </>
  );
}

function statusBadgeClass(status: FraudCaseStatus): string {
  if (status === "open") return "bg-amber-950 text-amber-400";
  if (status === "confirmed") return "bg-red-950 text-red-400";
  return "bg-emerald-950 text-emerald-400";
}

function FraudCasesSection({
  token,
  onError,
}: {
  token: string;
  onError: (message: string | null) => void;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<CaseStatusTab>("open");
  const [cases, setCases] = useState<FraudCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadCases = useCallback(() => {
    return Promise.resolve()
      .then(() => {
        setLoading(true);
        const status = tab === "all" ? undefined : tab;
        return listFraudCases(token, status);
      })
      .then((data) => {
        setCases(data);
        onError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        onError("Couldn't load fraud cases.");
      })
      .finally(() => setLoading(false));
  }, [token, tab, router, onError]);

  useEffect(() => {
    loadCases();
  }, [loadCases]);

  async function handleResolve(caseId: string, status: "confirmed" | "cleared") {
    setBusyId(caseId);
    try {
      await resolveFraudCase(token, caseId, status, notes || undefined);
      setResolvingId(null);
      setNotes("");
      await loadCases();
    } catch {
      onError("Couldn't resolve this case.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1 w-fit">
        {CASE_STATUS_TABS.map((t) => (
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

      {loading && <p className="text-sm text-slate-400">Loading...</p>}

      {!loading && cases.length === 0 && (
        <p className="text-sm text-slate-400">No {tab === "all" ? "" : tab} fraud cases.</p>
      )}

      <div className="space-y-3">
        {cases.map((c) => (
          <div key={c.id} className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-medium text-white">Account {c.account_id}</div>
                <div className="text-xs text-slate-400">
                  Opened {new Date(c.created_at).toLocaleString()}
                </div>
              </div>
              <span
                className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize ${statusBadgeClass(
                  c.status
                )}`}
              >
                {c.status}
              </span>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-slate-400">Risk score at open</div>
                <div className="text-slate-200">{c.score_at_open} / 100</div>
              </div>
              {c.resolved_by && (
                <div>
                  <div className="text-xs text-slate-400">Resolved by</div>
                  <div className="text-slate-200">{c.resolved_by}</div>
                </div>
              )}
            </div>

            {c.resolution_notes && (
              <div className="mt-3">
                <div className="text-xs text-slate-400 mb-1">Notes</div>
                <div className="text-sm text-slate-200">{c.resolution_notes}</div>
              </div>
            )}

            {c.status === "open" && (
              <div className="mt-4 pt-4 border-t border-slate-800">
                {resolvingId === c.id ? (
                  <div className="flex gap-2">
                    <input
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="Notes (optional)"
                      className="flex-1 rounded-lg border border-slate-700 bg-slate-800 text-white px-3 py-1.5 text-sm placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-500"
                    />
                    <button
                      onClick={() => handleResolve(c.id, "cleared")}
                      disabled={busyId === c.id}
                      className="text-sm bg-emerald-700 hover:bg-emerald-600 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 transition"
                    >
                      Clear
                    </button>
                    <button
                      onClick={() => handleResolve(c.id, "confirmed")}
                      disabled={busyId === c.id}
                      className="text-sm bg-red-700 hover:bg-red-600 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 transition"
                    >
                      Confirm fraud
                    </button>
                    <button
                      onClick={() => {
                        setResolvingId(null);
                        setNotes("");
                      }}
                      className="text-sm text-slate-400 hover:text-slate-200 px-2"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setResolvingId(c.id)}
                    className="text-sm bg-slate-800 hover:bg-slate-700 text-white rounded-lg px-4 py-1.5 transition"
                  >
                    Resolve
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

function FraudRulesSection({
  token,
  onError,
}: {
  token: string;
  onError: (message: string | null) => void;
}) {
  const router = useRouter();
  const [rules, setRules] = useState<FraudRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [drafts, setDrafts] = useState<Record<string, { weight: number; is_active: boolean }>>({});
  const [savingType, setSavingType] = useState<RiskSignalType | null>(null);

  const loadRules = useCallback(() => {
    return Promise.resolve()
      .then(() => {
        setLoading(true);
        return listFraudRules(token);
      })
      .then((data) => {
        setRules(data);
        onError(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearStaffToken();
          router.replace("/staff/login");
          return;
        }
        onError("Couldn't load fraud scoring rules.");
      })
      .finally(() => setLoading(false));
  }, [token, router, onError]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  function rowFor(signalType: RiskSignalType, defaultWeight: number) {
    const existing = rules.find((r) => r.signal_type === signalType);
    const draft = drafts[signalType];
    return {
      weight: draft?.weight ?? existing?.weight ?? defaultWeight,
      is_active: draft?.is_active ?? existing?.is_active ?? true,
      hasOverride: existing !== undefined,
    };
  }

  async function handleSave(signalType: RiskSignalType, defaultWeight: number) {
    const row = rowFor(signalType, defaultWeight);
    setSavingType(signalType);
    try {
      await upsertFraudRule(token, signalType, { weight: row.weight, is_active: row.is_active });
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[signalType];
        return next;
      });
      await loadRules();
    } catch (err) {
      onError(
        err instanceof ApiError && err.status === 403
          ? "Only super admins can change fraud scoring rules."
          : "Couldn't save this rule."
      );
    } finally {
      setSavingType(null);
    }
  }

  if (loading) return <p className="text-sm text-slate-400">Loading...</p>;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl divide-y divide-slate-800">
      {KNOWN_SIGNAL_TYPES.map(({ type, label, defaultWeight }) => {
        const row = rowFor(type, defaultWeight);
        return (
          <div key={type} className="p-5 flex items-center justify-between gap-4">
            <div>
              <div className="font-medium text-white">{label}</div>
              <div className="text-xs text-slate-400">
                {row.hasOverride ? "Custom weight" : `Default weight (${defaultWeight})`}
              </div>
            </div>

            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={row.is_active}
                  onChange={(e) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [type]: { weight: row.weight, is_active: e.target.checked },
                    }))
                  }
                  className="rounded border-slate-600 bg-slate-800"
                />
                Active
              </label>

              <input
                type="number"
                min={0}
                value={row.weight}
                onChange={(e) =>
                  setDrafts((prev) => ({
                    ...prev,
                    [type]: { weight: Number(e.target.value), is_active: row.is_active },
                  }))
                }
                className="w-20 rounded-lg border border-slate-700 bg-slate-800 text-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500"
              />

              <button
                onClick={() => handleSave(type, defaultWeight)}
                disabled={savingType === type}
                className="text-sm bg-slate-700 hover:bg-slate-600 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 transition"
              >
                {savingType === type ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
