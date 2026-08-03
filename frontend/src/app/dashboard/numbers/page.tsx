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
  ApiError,
  type ComplianceRule,
  type MyPhoneNumber,
  type NumberSearchResult,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

type Step = "search" | "reserved" | "compliance" | "checkout" | "purchased";

const STATUS_STYLES: Record<string, string> = {
  reserved: "bg-amber-50 text-amber-700",
  compliance_pending: "bg-orange-50 text-orange-700",
  purchase_pending: "bg-amber-50 text-amber-700",
  provisioning: "bg-amber-50 text-amber-700",
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
        await loadMyNumbers();
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

  async function handleSuspend(e164: string) {
    if (!token) return;
    setActionBusyE164(e164);
    setActionError(null);
    try {
      await suspendNumber(token, e164);
      await loadMyNumbers();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't suspend this number.");
    } finally {
      setActionBusyE164(null);
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
            <div
              key={n.id}
              className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3"
            >
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
                    onClick={() => handleSuspend(n.e164)}
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
