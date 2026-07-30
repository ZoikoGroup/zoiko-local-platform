"use client";

import { useState } from "react";
import { COUNTRIES, generateSampleNumbers } from "@/lib/sampleNumbers";
import { getComplianceRules, openComplianceCase, type ComplianceRule } from "@/lib/api";
import { getToken } from "@/lib/auth";

type Step = "search" | "reserved" | "compliance" | "checkout" | "not-connected";

export default function NumbersPage() {
  const [step, setStep] = useState<Step>("search");
  const [countryCode, setCountryCode] = useState("US");
  const [sampleResults, setSampleResults] = useState<string[]>([]);
  const [selectedNumber, setSelectedNumber] = useState<string | null>(null);

  const [complianceRules, setComplianceRules] = useState<ComplianceRule[]>([]);
  const [complianceChecked, setComplianceChecked] = useState(false);
  const [caseOpened, setCaseOpened] = useState(false);
  const [complianceError, setComplianceError] = useState<string | null>(null);

  function handleSearch() {
    setSampleResults(generateSampleNumbers(countryCode));
    setSelectedNumber(null);
  }

  function handleReserve(number: string) {
    setSelectedNumber(number);
    setStep("reserved");
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
    const token = getToken();
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

  const country = COUNTRIES.find((c) => c.code === countryCode);

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Get a Local Number</h2>
        <p className="text-sm text-slate-500">
          Search, reserve, and verify a number for your account.
        </p>
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-2 text-xs font-medium text-slate-400">
        {["Search", "Reserve", "Verify", "Checkout"].map((label, i) => {
          const stepIndex = ["search", "reserved", "compliance", "checkout"].indexOf(step);
          const active = i <= stepIndex || step === "not-connected";
          return (
            <div key={label} className="flex items-center gap-2">
              <span className={active ? "text-indigo-600" : ""}>{label}</span>
              {i < 3 && <span>&rarr;</span>}
            </div>
          );
        })}
      </div>

      {/* Step 1: Search */}
      {step === "search" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <div className="flex gap-3">
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
            <button
              onClick={handleSearch}
              className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
            >
              Search Numbers
            </button>
          </div>

          <p className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2">
            These are sample numbers for demonstration only — no real telecom provider
            is connected yet (Stage 2, waiting on Twilio access).
          </p>

          {sampleResults.length > 0 && (
            <ul className="space-y-2">
              {sampleResults.map((number) => (
                <li
                  key={number}
                  className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3"
                >
                  <span className="font-mono text-slate-800">{number}</span>
                  <button
                    onClick={() => handleReserve(number)}
                    className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
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
      {step === "reserved" && selectedNumber && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <div>
            <div className="text-sm text-slate-500">Reserved number</div>
            <div className="text-2xl font-mono font-semibold text-slate-900">{selectedNumber}</div>
          </div>
          <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
            Reservations hold a number for 12 minutes before it&apos;s released, matching
            the number lifecycle spec.
          </p>
          <button
            onClick={handleContinueToCompliance}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            Continue
          </button>
        </div>
      )}

      {/* Step 3: Compliance - this part is REAL, calls our actual backend */}
      {step === "compliance" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <h3 className="font-semibold text-slate-900">Identity Verification</h3>

          {!complianceChecked && <p className="text-sm text-slate-500">Checking requirements for {country?.name}...</p>}

          {complianceError && <p className="text-sm text-red-600">{complianceError}</p>}

          {complianceChecked && !complianceError && complianceRules.length === 0 && (
            <>
              <p className="text-sm text-slate-600">
                No ID verification is required for {country?.name}.
              </p>
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
                {country?.name} requires the following documents before this number can be
                activated:
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
                    Verification case opened — status: pending. Document upload isn&apos;t
                    built yet, so this stops here for now.
                  </p>
                  <button
                    onClick={() => setStep("checkout")}
                    className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
                  >
                    Continue Anyway (demo)
                  </button>
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* Step 4: Checkout - static, not real billing */}
      {step === "checkout" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <h3 className="font-semibold text-slate-900">Checkout</h3>
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Number</span>
            <span className="font-mono text-slate-800">{selectedNumber}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Monthly fee (estimate)</span>
            <span className="text-slate-800">$5.00 / month</span>
          </div>
          <button
            onClick={() => setStep("not-connected")}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            Confirm Purchase
          </button>
        </div>
      )}

      {/* Final: honest "not connected" state instead of a fake success message */}
      {step === "not-connected" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 text-center space-y-2">
          <h3 className="font-semibold text-slate-900">Not connected yet</h3>
          <p className="text-sm text-slate-500">
            This is as far as the demo goes — there&apos;s no real telecom provider or
            billing system wired up yet, so this purchase can&apos;t actually complete.
            The identity verification step before this, though, was real.
          </p>
          <button
            onClick={() => {
              setStep("search");
              setSampleResults([]);
              setSelectedNumber(null);
              setComplianceRules([]);
              setComplianceChecked(false);
              setCaseOpened(false);
            }}
            className="text-sm font-medium text-indigo-600"
          >
            Start over
          </button>
        </div>
      )}
    </div>
  );
}
