"use client";

import { useEffect, useState, useCallback } from "react";
import { COUNTRIES } from "@/lib/sampleNumbers";
import {
  getComplianceRules,
  openComplianceCase,
  listMyNumbers,
  searchNumbers,
  reserveNumber,
  createNumberCheckoutSession,
  suspendNumber,
  cancelNumber,
  configureRouting,
  listTeamMembers,
  acknowledgeEmergencyCallingLimitation,
  createPortingRequest,
  listMyPortingRequests,
  cancelPortingRequest,
  getRingGroup,
  setRingGroup,
  getIvrMenu,
  setIvrMenu,
  clearIvrMenu,
  ApiError,
  type ComplianceRule,
  type MyPhoneNumber,
  type NumberSearchResult,
  type TeamMember,
  type PortingRequest,
  type IVROption,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

type Step = "search" | "reserved" | "compliance" | "checkout";

const STATUS_STYLES: Record<string, string> = {
  reserved: "bg-amber-50 text-amber-700",
  compliance_pending: "bg-orange-50 text-orange-700",
  purchase_pending: "bg-amber-50 text-amber-700",
  provisioning: "bg-amber-50 text-amber-700",
  active: "bg-emerald-50 text-emerald-700",
  suspended: "bg-slate-100 text-slate-600",
  cancelled: "bg-red-50 text-red-700",
};

const PORTING_STATUS_STYLES: Record<string, string> = {
  submitted: "bg-amber-50 text-amber-700",
  approved: "bg-indigo-50 text-indigo-700",
  completed: "bg-emerald-50 text-emerald-700",
  rejected: "bg-red-50 text-red-700",
  canceled: "bg-slate-100 text-slate-600",
};

const EMPTY_PORTING_FORM = {
  phone_number: "",
  country: "US",
  current_carrier: "",
  carrier_account_number: "",
  billing_name: "",
  billing_address: "",
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
  const [routingWhatsapp, setRoutingWhatsapp] = useState(false);
  const [routingSms, setRoutingSms] = useState(false);
  const [routingBusy, setRoutingBusy] = useState(false);
  const [routingError, setRoutingError] = useState<string | null>(null);
  const [smsCaseBlockedE164, setSmsCaseBlockedE164] = useState<string | null>(null);
  const [smsCaseBusy, setSmsCaseBusy] = useState(false);
  const [smsCaseOpened, setSmsCaseOpened] = useState(false);

  const [ringGroupInput, setRingGroupInput] = useState("");
  const [ringGroupBusy, setRingGroupBusy] = useState(false);
  const [ringGroupError, setRingGroupError] = useState<string | null>(null);

  const [ivrGreetingInput, setIvrGreetingInput] = useState("");
  const [ivrOptionsInput, setIvrOptionsInput] = useState("");
  const [ivrBusy, setIvrBusy] = useState(false);
  const [ivrError, setIvrError] = useState<string | null>(null);
  const [ivrExisting, setIvrExisting] = useState<IVROption[]>([]);

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
  const [emergencyAcknowledged, setEmergencyAcknowledged] = useState(false);

  const [portingRequests, setPortingRequests] = useState<PortingRequest[]>([]);
  const [portingLoading, setPortingLoading] = useState(true);
  const [portingFormOpen, setPortingFormOpen] = useState(false);
  const [portingForm, setPortingForm] = useState(EMPTY_PORTING_FORM);
  const [portingBusy, setPortingBusy] = useState(false);
  const [portingError, setPortingError] = useState<string | null>(null);
  const [portingActionBusyId, setPortingActionBusyId] = useState<string | null>(null);

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

  // Stripe redirects the browser back here after Checkout (success or
  // cancel) - the number itself only activates via the backend's webhook,
  // not this redirect, so this just reflects that outcome to the customer
  // and refreshes the list to pick up the (by-then-likely-already-active)
  // number.
  const [checkoutResult, setCheckoutResult] = useState<"success" | "cancelled" | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const checkout = params.get("checkout");
    if (checkout === "success" || checkout === "cancelled") {
      window.history.replaceState({}, "", window.location.pathname);
      if (checkout === "success") loadMyNumbers();
      Promise.resolve().then(() => setCheckoutResult(checkout));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadPortingRequests = useCallback(() => {
    if (!token) return;
    return listMyPortingRequests(token)
      .then(setPortingRequests)
      .catch(() => {})
      .finally(() => setPortingLoading(false));
  }, [token]);

  useEffect(() => {
    loadPortingRequests();
  }, [loadPortingRequests]);

  async function handleSubmitPortingRequest(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setPortingBusy(true);
    setPortingError(null);
    try {
      await createPortingRequest(token, portingForm);
      setPortingForm(EMPTY_PORTING_FORM);
      setPortingFormOpen(false);
      await loadPortingRequests();
    } catch (err) {
      setPortingError(err instanceof ApiError ? err.message : "Couldn't submit this porting request.");
    } finally {
      setPortingBusy(false);
    }
  }

  async function handleCancelPortingRequest(requestId: string) {
    if (!token) return;
    setPortingActionBusyId(requestId);
    try {
      await cancelPortingRequest(token, requestId);
      await loadPortingRequests();
    } catch (err) {
      setPortingError(err instanceof ApiError ? err.message : "Couldn't cancel this request.");
    } finally {
      setPortingActionBusyId(null);
    }
  }

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
    if (!token || !reservedNumber || !emergencyAcknowledged) return;
    setPurchaseBusy(true);
    setPurchaseError(null);
    try {
      await acknowledgeEmergencyCallingLimitation(token);
      // Real Stripe Checkout (test mode) - redirects to Stripe's own
      // hosted payment page. The number only actually activates once
      // Stripe confirms payment via the backend's webhook; this tab
      // navigates away, so nothing more happens here on success.
      const session = await createNumberCheckoutSession(token, reservedNumber.e164);
      window.location.href = session.url;
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
            ? `Couldn't start checkout: ${err.message}`
            : "Couldn't start checkout."
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
    setEmergencyAcknowledged(false);
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
    setRoutingWhatsapp(number.whatsapp_enabled);
    setRoutingSms(number.sms_enabled);
    setSmsCaseBlockedE164(null);
    setSmsCaseOpened(false);

    setRingGroupError(null);
    setRingGroupInput("");
    if (token) {
      getRingGroup(token, number.e164)
        .then((destinations) => setRingGroupInput(destinations.map((d) => d.destination_number).join(", ")))
        .catch(() => setRingGroupError("Couldn't load the ring group."));
    }

    setIvrError(null);
    setIvrGreetingInput("");
    setIvrOptionsInput("");
    setIvrExisting([]);
    if (token) {
      getIvrMenu(token, number.e164)
        .then((menu) => {
          setIvrGreetingInput(menu.greeting ?? "");
          setIvrExisting(menu.options);
          setIvrOptionsInput(menu.options.map((o) => `${o.digit}=${o.destination_number}`).join(", "));
        })
        .catch(() => setIvrError("Couldn't load the IVR menu."));
    }
  }

  async function handleSaveRingGroup(e164: string) {
    if (!token) return;
    setRingGroupBusy(true);
    setRingGroupError(null);
    try {
      const destinations = ringGroupInput
        .split(",")
        .map((n) => n.trim())
        .filter(Boolean);
      await setRingGroup(token, e164, destinations);
    } catch (err) {
      setRingGroupError(err instanceof ApiError ? err.message : "Couldn't save the ring group.");
    } finally {
      setRingGroupBusy(false);
    }
  }

  function parseIvrOptionsInput(input: string): Record<string, string> {
    const options: Record<string, string> = {};
    for (const pair of input.split(",")) {
      const [digit, destination] = pair.split("=").map((s) => s.trim());
      if (digit && destination) options[digit] = destination;
    }
    return options;
  }

  async function handleSaveIvrMenu(e164: string) {
    if (!token || !ivrGreetingInput.trim()) return;
    setIvrBusy(true);
    setIvrError(null);
    try {
      const options = parseIvrOptionsInput(ivrOptionsInput);
      const menu = await setIvrMenu(token, e164, ivrGreetingInput, options);
      setIvrExisting(menu.options);
    } catch (err) {
      setIvrError(err instanceof ApiError ? err.message : "Couldn't save the IVR menu.");
    } finally {
      setIvrBusy(false);
    }
  }

  async function handleClearIvrMenu(e164: string) {
    if (!token) return;
    setIvrBusy(true);
    setIvrError(null);
    try {
      await clearIvrMenu(token, e164);
      setIvrGreetingInput("");
      setIvrOptionsInput("");
      setIvrExisting([]);
    } catch (err) {
      setIvrError(err instanceof ApiError ? err.message : "Couldn't clear the IVR menu.");
    } finally {
      setIvrBusy(false);
    }
  }

  async function handleSaveRouting(e164: string) {
    if (!token) return;
    setRoutingBusy(true);
    setRoutingError(null);
    setSmsCaseBlockedE164(null);
    try {
      await configureRouting(token, e164, {
        forwarding_number: routingForwarding || null,
        business_hours_start: routingHoursStart || null,
        business_hours_end: routingHoursEnd || null,
        business_hours_timezone: routingTimezone || "UTC",
        ai_receptionist_enabled: routingReceptionist,
        escalation_user_id: routingEscalationUserId || null,
        whatsapp_enabled: routingWhatsapp,
        sms_enabled: routingSms,
      });
      setRoutingOpenE164(null);
      await loadMyNumbers();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403 && err.message.includes("sms_business_messaging")) {
        setSmsCaseBlockedE164(e164);
      }
      setRoutingError(err instanceof ApiError ? err.message : "Couldn't save routing settings.");
    } finally {
      setRoutingBusy(false);
    }
  }

  async function handleOpenSmsComplianceCase(country: string) {
    if (!token) return;
    setSmsCaseBusy(true);
    try {
      await openComplianceCase(token, { jurisdiction: country, requirement_type: "sms_business_messaging" });
      setSmsCaseOpened(true);
    } catch (err) {
      setRoutingError(err instanceof ApiError ? err.message : "Couldn't open a compliance case.");
    } finally {
      setSmsCaseBusy(false);
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

      {checkoutResult === "success" && (
        <p className="text-sm text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
          Payment received — your number will show as active below shortly (test mode, no real charge was
          made).
        </p>
      )}
      {checkoutResult === "cancelled" && (
        <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          Checkout was cancelled — no payment was made and your number wasn&apos;t purchased. Your
          reservation may still be active below if you&apos;d like to try again.
        </p>
      )}

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

                  {smsCaseBlockedE164 === n.e164 && (
                    <div className="text-xs bg-orange-50 text-orange-700 rounded-lg px-3 py-2 space-y-1.5">
                      {smsCaseOpened ? (
                        <p>
                          Compliance case opened for {n.country} SMS registration - it needs staff approval before
                          SMS can be enabled. Track its status on the{" "}
                          <a href="/dashboard/settings" className="underline font-medium">
                            Settings
                          </a>{" "}
                          page.
                        </p>
                      ) : (
                        <div className="flex items-center justify-between gap-3">
                          <span>{n.country} requires an approved SMS business messaging compliance case.</span>
                          <button
                            onClick={() => handleOpenSmsComplianceCase(n.country)}
                            disabled={smsCaseBusy}
                            className="font-medium underline shrink-0 disabled:opacity-60"
                          >
                            {smsCaseBusy ? "Opening…" : "Open compliance case"}
                          </button>
                        </div>
                      )}
                    </div>
                  )}

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

                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={routingWhatsapp}
                      onChange={(e) => setRoutingWhatsapp(e.target.checked)}
                    />
                    WhatsApp Business approved for this number
                  </label>
                  <p className="text-xs text-slate-400 -mt-2">
                    Only check this once Meta/Twilio have approved this number as a WhatsApp Business sender -
                    it does not request or grant that approval itself.
                  </p>

                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={routingSms}
                      onChange={(e) => setRoutingSms(e.target.checked)}
                    />
                    SMS business messaging registered for this number
                  </label>
                  <p className="text-xs text-slate-400 -mt-2">
                    Only check this once A2P 10DLC brand/campaign registration is complete for this number -
                    it does not request or grant that registration itself.
                  </p>

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

                  <div className="border-t border-slate-200 pt-3 space-y-2">
                    <label className="block text-xs font-medium text-slate-500">
                      Ring group (rings all of these at once instead of the single forwarding number above)
                    </label>
                    {ringGroupError && <p className="text-xs text-red-600">{ringGroupError}</p>}
                    <input
                      value={ringGroupInput}
                      onChange={(e) => setRingGroupInput(e.target.value)}
                      placeholder="+15551112222, +15553334444"
                      className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-mono placeholder:text-slate-400"
                    />
                    <p className="text-xs text-slate-400">
                      Comma-separated, up to 5 numbers. Leave empty to use the forwarding number instead.
                    </p>
                    <button
                      onClick={() => handleSaveRingGroup(n.e164)}
                      disabled={ringGroupBusy}
                      className="bg-slate-700 hover:bg-slate-800 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
                    >
                      {ringGroupBusy ? "Saving..." : "Save ring group"}
                    </button>
                  </div>

                  <div className="border-t border-slate-200 pt-3 space-y-2">
                    <label className="block text-xs font-medium text-slate-500">
                      IVR menu (plays a greeting and routes by keypad digit before forwarding/ring group)
                    </label>
                    {ivrError && <p className="text-xs text-red-600">{ivrError}</p>}
                    <input
                      value={ivrGreetingInput}
                      onChange={(e) => setIvrGreetingInput(e.target.value)}
                      placeholder="Press 1 for sales, 2 for support."
                      className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm placeholder:text-slate-400"
                    />
                    <input
                      value={ivrOptionsInput}
                      onChange={(e) => setIvrOptionsInput(e.target.value)}
                      placeholder="1=+15551112222, 2=+15553334444"
                      className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-mono placeholder:text-slate-400"
                    />
                    <p className="text-xs text-slate-400">
                      Digit=destination pairs, comma-separated, up to 10. An unrecognized digit or no input falls
                      through to the routing above.
                      {ivrExisting.length > 0 && ` Currently ${ivrExisting.length} option(s) configured.`}
                    </p>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleSaveIvrMenu(n.e164)}
                        disabled={ivrBusy || !ivrGreetingInput.trim()}
                        className="bg-slate-700 hover:bg-slate-800 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
                      >
                        {ivrBusy ? "Saving..." : "Save IVR menu"}
                      </button>
                      {ivrExisting.length > 0 && (
                        <button
                          onClick={() => handleClearIvrMenu(n.e164)}
                          disabled={ivrBusy}
                          className="text-sm text-red-600 hover:text-red-700 font-medium disabled:opacity-50"
                        >
                          Clear
                        </button>
                      )}
                    </div>
                  </div>
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

      <div className="flex items-center gap-2 text-xs font-medium text-slate-600">
        {["Search", "Reserve", "Verify", "Checkout"].map((label, i) => {
          const stepIndex = ["search", "reserved", "compliance", "checkout"].indexOf(step);
          const active = i <= stepIndex;
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
            <label htmlFor="search-country" className="sr-only">
              Country to search
            </label>
            <select
              id="search-country"
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
            <span className="text-slate-500">Purchase price</span>
            <span className="text-slate-800">$1.00 (test mode — no real charge)</span>
          </div>
          <p className="text-xs text-slate-400">
            You&apos;ll be redirected to Stripe&apos;s secure payment page to complete this purchase.
          </p>

          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2">
            <p className="text-xs text-amber-900 font-medium">Emergency calling notice</p>
            <p className="text-xs text-amber-800">
              This service is not a replacement for a traditional phone line. Emergency calling (911, 999,
              or your local equivalent) may not work reliably, may not transmit your location, or may not
              work at all during a power or internet outage. Do not rely on this number for emergency
              calls.
            </p>
            <label className="flex items-start gap-2 text-xs text-amber-900 cursor-pointer">
              <input
                type="checkbox"
                checked={emergencyAcknowledged}
                onChange={(e) => setEmergencyAcknowledged(e.target.checked)}
                className="mt-0.5"
              />
              I understand and accept this limitation.
            </label>
          </div>

          {purchaseError && (
            <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{purchaseError}</p>
          )}

          <button
            onClick={handlePurchase}
            disabled={purchaseBusy || !emergencyAcknowledged}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            {purchaseBusy ? "Redirecting to payment..." : "Proceed to Payment"}
          </button>
        </div>
      )}

      {/* Port an existing number */}
      <div>
        <h3 className="text-lg font-semibold text-slate-900">Already Have a Number?</h3>
        <p className="text-sm text-slate-500">
          Port a number you own with another carrier over to Zoiko Local.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
          Porting moves a real number between carriers, so it&apos;s reviewed by our team and coordinated with
          your current carrier by hand — it isn&apos;t instant like a new number purchase. We&apos;ll email you at
          each step.
        </p>

        {portingLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {portingError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{portingError}</p>}

        {portingRequests.length > 0 && (
          <div className="space-y-2">
            {portingRequests.map((r) => (
              <div
                key={r.id}
                className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3"
              >
                <div>
                  <span className="font-mono text-slate-800">{r.phone_number}</span>
                  <span
                    className={`ml-3 text-xs font-medium rounded-full px-2 py-0.5 capitalize ${
                      PORTING_STATUS_STYLES[r.status] ?? "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {r.status}
                  </span>
                  {r.status === "rejected" && r.rejection_reason && (
                    <p className="text-xs text-red-600 mt-1">{r.rejection_reason}</p>
                  )}
                </div>
                {(r.status === "submitted" || r.status === "approved") && (
                  <button
                    onClick={() => handleCancelPortingRequest(r.id)}
                    disabled={portingActionBusyId === r.id}
                    className="text-xs font-medium text-slate-500 hover:text-slate-800 disabled:opacity-60"
                  >
                    Cancel
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {!portingFormOpen ? (
          <button
            onClick={() => setPortingFormOpen(true)}
            className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
          >
            + Request a number port
          </button>
        ) : (
          <form onSubmit={handleSubmitPortingRequest} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">Phone number</label>
                <input
                  required
                  value={portingForm.phone_number}
                  onChange={(e) => setPortingForm({ ...portingForm, phone_number: e.target.value })}
                  placeholder="+15551234567"
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-mono placeholder:text-slate-400"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">Country</label>
                <select
                  value={portingForm.country}
                  onChange={(e) => setPortingForm({ ...portingForm, country: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                >
                  {COUNTRIES.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">Current carrier</label>
                <input
                  required
                  value={portingForm.current_carrier}
                  onChange={(e) => setPortingForm({ ...portingForm, current_carrier: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  Account number with current carrier
                </label>
                <input
                  required
                  value={portingForm.carrier_account_number}
                  onChange={(e) => setPortingForm({ ...portingForm, carrier_account_number: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">Billing name</label>
                <input
                  required
                  value={portingForm.billing_name}
                  onChange={(e) => setPortingForm({ ...portingForm, billing_name: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">Billing address</label>
                <input
                  required
                  value={portingForm.billing_address}
                  onChange={(e) => setPortingForm({ ...portingForm, billing_address: e.target.value })}
                  className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                />
              </div>
            </div>

            <div className="flex gap-2">
              <button
                type="submit"
                disabled={portingBusy}
                className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
              >
                {portingBusy ? "Submitting..." : "Submit Porting Request"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setPortingFormOpen(false);
                  setPortingForm(EMPTY_PORTING_FORM);
                  setPortingError(null);
                }}
                className="text-sm text-slate-500 hover:text-slate-700 px-2"
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
