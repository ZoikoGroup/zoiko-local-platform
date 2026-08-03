"use client";

import { useEffect, useState, useCallback } from "react";
import { COUNTRIES } from "@/lib/sampleNumbers";
import {
  getComplianceRules,
  openComplianceCase,
  listMyNumbers,
  searchNumbers,
  reserveNumber,
  purchaseNumber,
  suspendNumber,
  cancelNumber,
  configureRouting,
  listTeamMembers,
  ApiError,
  type ComplianceRule,
  type MyPhoneNumber,
  type NumberSearchResult,
  type TeamMember,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

type Step = "search" | "reserved" | "compliance" | "checkout" | "purchased";

const STATUS_STYLES: Record<string, string> = {
  reserved: "bg-amber-50 text-amber-700",
  purchase_pending: "bg-amber-50 text-amber-700",
  active: "bg-emerald-50 text-emerald-700",
  suspended: "bg-slate-100 text-slate-600",
  cancelled: "bg-red-50 text-red-700",
};

export default function NumbersPage() {
  const [token] = useState<string | null>(() => getToken());

  const [myNumbers, setMyNumbers] = useState<MyPhoneNumber[]>([]);
  const [myNumbersLoading, setMyNumbersLoading] = useState(true);
  const [myNumbersError, setMyNumbersError] = useState<string | null>(null);
  const [actionBusyE164, setActionBusyE164] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);

  const [suspendingE164, setSuspendingE164] = useState<string | null>(null);
  const [suspendReason, setSuspendReason] = useState("");

  const [routingOpenE164, setRoutingOpenE164] = useState<string | null>(null);
  const [routingForwarding, setRoutingForwarding] = useState("");
  const [routingHoursStart, setRoutingHoursStart] = useState("");
  const [routingHoursEnd, setRoutingHoursEnd] = useState("");
  const [routingTimezone, setRoutingTimezone] = useState("UTC");
  const [routingReceptionist, setRoutingReceptionist] = useState(false);
  const [routingEscalationUserId, setRoutingEscalationUserId] = useState("");
  const [routingBusy, setRoutingBusy] = useState(false);
  const [routingError, setRoutingError] = useState<string | null>(null);

  const [step, setStep] = useState<Step>("search");
  const [countryCode, setCountryCode] = useState("US");
  const [areaCode, setAreaCode] = useState("");
  const [searchResults, setSearchResults] = useState<NumberSearchResult[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [reservedNumber, setReservedNumber] = useState<MyPhoneNumber | null>(null);
  const [reserveBusy, setReserveBusy] = useState(false);
  const [reserveError, setReserveError] = useState<string | null>(null);

  const [complianceRules, setComplianceRules] = useState<ComplianceRule[]>([]);
  const [complianceChecked, setComplianceChecked] = useState(false);
  const [caseOpened, setCaseOpened] = useState(false);
  const [complianceError, setComplianceError] = useState<string | null>(null);

  const [purchaseBusy, setPurchaseBusy] = useState(false);
  const [purchaseError, setPurchaseError] = useState<string | null>(null);
  const [purchasedNumber, setPurchasedNumber] = useState<MyPhoneNumber | null>(null);

  const loadMyNumbers = useCallback(() => {
    if (!token) return;
    return listMyNumbers(token)
      .then((data) => {
        setMyNumbers(data);
        setMyNumbersError(null);
      })
      .catch(() => setMyNumbersError("Couldn't load your numbers."))
      .finally(() => setMyNumbersLoading(false));
  }, [token]);

  useEffect(() => {
    loadMyNumbers();
  }, [loadMyNumbers]);

  useEffect(() => {
    if (!token) return;
    // Best-effort: a plain Member gets a 403 here (team roster is Owner/Admin
    // only) - that's fine, the escalation dropdown just stays empty for them.
    listTeamMembers(token)
      .then(setTeamMembers)
      .catch(() => {});
  }, [token]);

  async function handleSearch() {
    if (!token) return;
    setSearchBusy(true);
    setSearchError(null);
    setSearchResults([]);
    try {
      setSearchResults(await searchNumbers(token, { country: countryCode, area_code: areaCode || undefined }));
    } catch (err) {
      setSearchError(
        err instanceof ApiError
          ? `Couldn't search numbers: ${err.message}`
          : "Couldn't reach the telecom provider."
      );
    } finally {
      setSearchBusy(false);
    }
  }

  async function handleReserve(phoneNumber: string) {
    if (!token) return;
    setReserveBusy(true);
    setReserveError(null);
    try {
      const number = await reserveNumber(token, { e164: phoneNumber, country: countryCode });
      setReservedNumber(number);
      setStep("reserved");
    } catch (err) {
      setReserveError(err instanceof ApiError ? err.message : "Couldn't reserve this number.");
    } finally {
      setReserveBusy(false);
    }
  }

  async function handleContinueToCompliance() {
    setStep("compliance");
    setComplianceChecked(false);
    setComplianceError(null);
    try {
      const rules = await getComplianceRules(countryCode);
      setComplianceRules(rules);
    } catch {
      setComplianceError("Couldn't reach the compliance service.");
    } finally {
      setComplianceChecked(true);
    }
  }

  async function handleStartVerification() {
    if (!token) return;
    try {
      await openComplianceCase(token, {
        jurisdiction: countryCode,
        requirement_type: complianceRules[0]?.requirement_type ?? "kyc_individual",
      });
      setCaseOpened(true);
    } catch {
      setComplianceError("Couldn't open a verification case.");
    }
  }

  async function handlePurchase() {
    if (!token || !reservedNumber) return;
    setPurchaseBusy(true);
    setPurchaseError(null);
    try {
      const number = await purchaseNumber(token, reservedNumber.e164);
      setPurchasedNumber(number);
      setStep("purchased");
      await loadMyNumbers();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setPurchaseError(
          "This number's country needs an approved identity verification case before it can be purchased — " +
            "your case is still pending staff review."
        );
      } else if (err instanceof ApiError && err.status === 409) {
        setPurchaseError(`${err.message} — go back and reserve a number again.`);
      } else {
        setPurchaseError(
          err instanceof ApiError
            ? `Couldn't reach the telecom provider: ${err.message}`
            : "Couldn't complete the purchase."
        );
      }
    } finally {
      setPurchaseBusy(false);
    }
  }

  function handleStartOver() {
    setStep("search");
    setSearchResults([]);
    setSearchError(null);
    setReservedNumber(null);
    setReserveError(null);
    setComplianceRules([]);
    setComplianceChecked(false);
    setCaseOpened(false);
    setComplianceError(null);
    setPurchaseError(null);
    setPurchasedNumber(null);
  }

  async function handleConfirmSuspend(e164: string) {
    if (!token) return;
    setActionBusyE164(e164);
    setActionError(null);
    try {
      await suspendNumber(token, e164, suspendReason || undefined);
      setSuspendingE164(null);
      setSuspendReason("");
      await loadMyNumbers();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't suspend this number.");
    } finally {
      setActionBusyE164(null);
    }
  }

  function handleToggleRouting(number: MyPhoneNumber) {
    if (routingOpenE164 === number.e164) {
      setRoutingOpenE164(null);
      return;
    }
    setRoutingOpenE164(number.e164);
    setRoutingError(null);
    setRoutingForwarding(number.forwarding_number ?? "");
    setRoutingHoursStart(number.business_hours_start ?? "");
    setRoutingHoursEnd(number.business_hours_end ?? "");
    setRoutingTimezone(number.business_hours_timezone || "UTC");
    setRoutingReceptionist(number.ai_receptionist_enabled);
    setRoutingEscalationUserId(number.escalation_user_id ?? "");
  }

  async function handleSaveRouting(e164: string) {
    if (!token) return;
    setRoutingBusy(true);
    setRoutingError(null);
    try {
      await configureRouting(token, e164, {
        forwarding_number: routingForwarding || null,
        business_hours_start: routingHoursStart || null,
        business_hours_end: routingHoursEnd || null,
        business_hours_timezone: routingTimezone || "UTC",
        ai_receptionist_enabled: routingReceptionist,
        escalation_user_id: routingEscalationUserId || null,
      });
      setRoutingOpenE164(null);
      await loadMyNumbers();
    } catch (err) {
      setRoutingError(err instanceof ApiError ? err.message : "Couldn't save routing settings.");
    } finally {
      setRoutingBusy(false);
    }
  }

  async function handleCancel(e164: string) {
    if (!token) return;
    setActionBusyE164(e164);
    setActionError(null);
    try {
      await cancelNumber(token, e164);
      await loadMyNumbers();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't cancel this number.");
    } finally {
      setActionBusyE164(null);
    }
  }

  const country = COUNTRIES.find((c) => c.code === countryCode);

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* My Numbers */}
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Phone Numbers</h2>
        <p className="text-sm text-slate-500">Numbers your account owns, and getting a new one.</p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">My Numbers</h3>

        {myNumbersLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {myNumbersError && <p className="text-sm text-red-600">{myNumbersError}</p>}
        {!myNumbersLoading && myNumbers.length === 0 && (
          <p className="text-sm text-slate-500">You don&apos;t have any numbers yet — get one below.</p>
        )}
        {actionError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{actionError}</p>}

        <div className="space-y-2">
          {myNumbers.map((n) => (
            <div key={n.id} className="rounded-lg border border-slate-200 px-4 py-3 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-mono text-slate-800">{n.e164}</span>
                  <span
                    className={`ml-3 text-xs font-medium rounded-full px-2 py-0.5 capitalize ${
                      STATUS_STYLES[n.status] ?? "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {n.status.replaceAll("_", " ")}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  {n.status === "active" && (
                    <button
                      onClick={() => handleToggleRouting(n)}
                      className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                    >
                      {routingOpenE164 === n.e164 ? "Close routing" : "Routing"}
                    </button>
                  )}
                  {n.status === "active" && (
                    <button
                      onClick={() => setSuspendingE164(suspendingE164 === n.e164 ? null : n.e164)}
                      disabled={actionBusyE164 === n.e164}
                      className="text-xs font-medium text-slate-500 hover:text-slate-800 disabled:opacity-60"
                    >
                      Suspend
                    </button>
                  )}
                  {(n.status === "active" || n.status === "suspended") && (
                    <button
                      onClick={() => handleCancel(n.e164)}
                      disabled={actionBusyE164 === n.e164}
                      className="text-xs font-medium text-red-600 hover:text-red-800 disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </div>

              {suspendingE164 === n.e164 && (
                <div className="flex items-center gap-2 bg-slate-50 rounded-lg px-3 py-2">
                  <input
                    value={suspendReason}
                    onChange={(e) => setSuspendReason(e.target.value)}
                    placeholder="Reason (optional)"
                    className="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-sm placeholder:text-slate-400"
                  />
                  <button
                    onClick={() => handleConfirmSuspend(n.e164)}
                    disabled={actionBusyE164 === n.e164}
                    className="text-xs font-medium bg-slate-800 hover:bg-slate-900 disabled:opacity-60 text-white rounded-lg px-3 py-1.5"
                  >
                    Confirm suspend
                  </button>
                  <button
                    onClick={() => {
                      setSuspendingE164(null);
                      setSuspendReason("");
                    }}
                    className="text-xs text-slate-400 hover:text-slate-600"
                  >
                    Cancel
                  </button>
                </div>
              )}

              {routingOpenE164 === n.e164 && (
                <div className="bg-slate-50 rounded-lg p-4 space-y-3">
                  {routingError && <p className="text-xs text-red-600">{routingError}</p>}

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Forwarding number</label>
                      <input
                        value={routingForwarding}
                        onChange={(e) => setRoutingForwarding(e.target.value)}
                        placeholder="+15551234567"
                        className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-mono placeholder:text-slate-400"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Timezone</label>
                      <input
                        value={routingTimezone}
                        onChange={(e) => setRoutingTimezone(e.target.value)}
                        placeholder="UTC"
                        className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm placeholder:text-slate-400"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Business hours start</label>
                      <input
                        type="time"
                        value={routingHoursStart}
                        onChange={(e) => setRoutingHoursStart(e.target.value)}
                        className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Business hours end</label>
                      <input
                        type="time"
                        value={routingHoursEnd}
                        onChange={(e) => setRoutingHoursEnd(e.target.value)}
                        className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                      />
                    </div>
                  </div>

                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={routingReceptionist}
                      onChange={(e) => setRoutingReceptionist(e.target.checked)}
                    />
                    Enable AI receptionist outside forwarding
                  </label>

                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">
                      Escalate urgent calls to
                    </label>
                    <select
                      value={routingEscalationUserId}
                      onChange={(e) => setRoutingEscalationUserId(e.target.value)}
                      className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                    >
                      <option value="">No one nominated</option>
                      {teamMembers.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.email} ({m.role})
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-slate-400 mt-1">
                      Only a nominated team member triggers live escalation for urgent receptionist calls.
                    </p>
                  </div>

                  <button
                    onClick={() => handleSaveRouting(n.e164)}
                    disabled={routingBusy}
                    className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
                  >
                    {routingBusy ? "Saving..." : "Save routing"}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Get a new number */}
      <div>
        <h3 className="text-lg font-semibold text-slate-900">Get a Local Number</h3>
        <p className="text-sm text-slate-500">Search, reserve, and verify a number for your account.</p>
      </div>

      <div className="flex items-center gap-2 text-xs font-medium text-slate-400">
        {["Search", "Reserve", "Verify", "Checkout"].map((label, i) => {
          const stepIndex = ["search", "reserved", "compliance", "checkout"].indexOf(step);
          const active = i <= stepIndex || step === "purchased";
          return (
            <div key={label} className="flex items-center gap-2">
              <span className={active ? "text-indigo-600" : ""}>{label}</span>
              {i < 3 && <span>&rarr;</span>}
            </div>
          );
        })}
      </div>

      {/* Step 1: Search - real /numbers/search call */}
      {step === "search" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <div className="flex flex-wrap gap-3">
            <select
              value={countryCode}
              onChange={(e) => setCountryCode(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              {COUNTRIES.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.name} ({c.dial})
                </option>
              ))}
            </select>
            <input
              value={areaCode}
              onChange={(e) => setAreaCode(e.target.value)}
              placeholder="Area code (optional)"
              className="w-40 rounded-lg border border-slate-300 px-3 py-2 text-sm placeholder:text-slate-400"
            />
            <button
              onClick={handleSearch}
              disabled={searchBusy}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
            >
              {searchBusy ? "Searching..." : "Search Numbers"}
            </button>
          </div>

          {searchError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{searchError}</p>}
          {reserveError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{reserveError}</p>}

          {searchResults.length > 0 && (
            <ul className="space-y-2">
              {searchResults.map((result) => (
                <li
                  key={result.phone_number}
                  className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3"
                >
                  <div>
                    <span className="font-mono text-slate-800">{result.phone_number}</span>
                    {result.locality && (
                      <span className="ml-2 text-xs text-slate-400">
                        {result.locality}
                        {result.region ? `, ${result.region}` : ""}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => handleReserve(result.phone_number)}
                    disabled={reserveBusy}
                    className="text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-60"
                  >
                    Reserve
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Step 2: Reserved */}
      {step === "reserved" && reservedNumber && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <div>
            <div className="text-sm text-slate-500">Reserved number</div>
            <div className="text-2xl font-mono font-semibold text-slate-900">{reservedNumber.e164}</div>
          </div>
          <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
            {reservedNumber.reserved_until
              ? `Held until ${new Date(reservedNumber.reserved_until).toLocaleTimeString()} — reserve again if it expires first.`
              : "Reservations hold a number for a short window before it's released."}
          </p>
          <button
            onClick={handleContinueToCompliance}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            Continue
          </button>
        </div>
      )}

      {/* Step 3: Compliance - real backend calls */}
      {step === "compliance" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <h3 className="font-semibold text-slate-900">Identity Verification</h3>

          {!complianceChecked && <p className="text-sm text-slate-500">Checking requirements for {country?.name}...</p>}

          {complianceError && <p className="text-sm text-red-600">{complianceError}</p>}

          {complianceChecked && !complianceError && complianceRules.length === 0 && (
            <>
              <p className="text-sm text-slate-600">No ID verification is required for {country?.name}.</p>
              <button
                onClick={() => setStep("checkout")}
                className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
              >
                Continue to Checkout
              </button>
            </>
          )}

          {complianceChecked && !complianceError && complianceRules.length > 0 && (
            <>
              <p className="text-sm text-slate-600">
                {country?.name} requires the following documents before this number can be activated:
              </p>
              <ul className="list-disc list-inside text-sm text-slate-700">
                {complianceRules[0].required_documents.map((doc) => (
                  <li key={doc}>{doc.replaceAll("_", " ")}</li>
                ))}
              </ul>

              {!caseOpened ? (
                <button
                  onClick={handleStartVerification}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
                >
                  Start Verification
                </button>
              ) : (
                <>
                  <p className="text-sm text-emerald-600 bg-emerald-50 rounded-lg px-3 py-2">
                    Verification case opened — status: pending, awaiting staff review.
                  </p>
                  <button
                    onClick={() => setStep("checkout")}
                    className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
                  >
                    Continue to Checkout
                  </button>
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* Step 4: Checkout - real /numbers/purchase call */}
      {step === "checkout" && reservedNumber && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <h3 className="font-semibold text-slate-900">Checkout</h3>
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Number</span>
            <span className="font-mono text-slate-800">{reservedNumber.e164}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Monthly fee (estimate)</span>
            <span className="text-slate-800">$5.00 / month</span>
          </div>

          {purchaseError && (
            <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{purchaseError}</p>
          )}

          <button
            onClick={handlePurchase}
            disabled={purchaseBusy}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            {purchaseBusy ? "Purchasing..." : "Confirm Purchase"}
          </button>
        </div>
      )}

      {/* Final: real purchase result */}
      {step === "purchased" && purchasedNumber && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 text-center space-y-2">
          <h3 className="font-semibold text-slate-900">Number activated</h3>
          <p className="text-sm text-slate-500">
            <span className="font-mono text-slate-800">{purchasedNumber.e164}</span> is now active on your
            account.
          </p>
          <button onClick={handleStartOver} className="text-sm font-medium text-indigo-600">
            Get another number
          </button>
        </div>
      )}
    </div>
  );
}
