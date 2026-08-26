"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { Call, Device } from "@twilio/voice-sdk";
import {
  listMyNumbers,
  listCalls,
  placeOutboundCall,
  placeBridgeCall,
  getBrowserVoiceToken,
  listVoicemails,
  summarizeCall,
  summarizeVoicemail,
  grantAiProcessingConsent,
  listContacts,
  ApiError,
  type MyPhoneNumber,
  type CallLogEntry,
  type VoicemailEntry,
  type Contact,
} from "@/lib/api";
import { getToken } from "@/lib/auth";
import { CallRow, type SummaryKey, type SummaryState } from "@/components/CallRow";

type BrowserCallStatus = "idle" | "connecting" | "ringing" | "in-call" | "ended" | "error";

export default function CallsPage() {
  const [token] = useState<string | null>(() => getToken());

  const [numbers, setNumbers] = useState<MyPhoneNumber[]>([]);
  const [calls, setCalls] = useState<CallLogEntry[]>([]);
  const [voicemails, setVoicemails] = useState<VoicemailEntry[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [toNumber, setToNumber] = useState("");
  const [fromNumber, setFromNumber] = useState("");
  const [agentNumber, setAgentNumber] = useState("");
  const [callMessage, setCallMessage] = useState("");
  const [callBusy, setCallBusy] = useState(false);
  const [callError, setCallError] = useState<string | null>(null);
  const [callSuccess, setCallSuccess] = useState<string | null>(null);

  const [summaries, setSummaries] = useState<Record<SummaryKey, SummaryState>>({});

  const deviceRef = useRef<Device | null>(null);
  const activeCallRef = useRef<Call | null>(null);
  const [browserCallStatus, setBrowserCallStatus] = useState<BrowserCallStatus>("idle");
  const [browserCallError, setBrowserCallError] = useState<string | null>(null);

  const loadAll = useCallback(() => {
    if (!token) return;
    return Promise.all([listMyNumbers(token), listCalls(token), listVoicemails(token), listContacts(token)])
      .then(([numbersData, callsData, voicemailsData, contactsData]) => {
        setNumbers(numbersData);
        setCalls(callsData);
        setVoicemails(voicemailsData);
        setContacts(contactsData);
        setLoadError(null);
        setFromNumber((current) => current || numbersData.find((n) => n.status === "active")?.e164 || "");
      })
      .catch(() => setLoadError("Couldn't load calls."))
      .finally(() => setLoading(false));
  }, [token]);

  // Resolves a raw phone number to a saved contact's name, falling back to
  // the bare number - built once per contacts load rather than searching
  // the array on every row render.
  const contactNameByPhone = contacts.reduce<Record<string, string>>((acc, c) => {
    acc[c.phone_number] = c.name;
    return acc;
  }, {});
  function displayNumber(phone: string): string {
    return contactNameByPhone[phone] ?? phone;
  }

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function handlePlaceCall(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setCallBusy(true);
    setCallError(null);
    setCallSuccess(null);
    try {
      const result = await placeOutboundCall(token, {
        to: toNumber,
        from: fromNumber,
        ...(callMessage.trim() ? { message: callMessage.trim() } : {}),
      });
      setCallSuccess(`Call placed to ${result.to} — status: ${result.status}`);
      setToNumber("");
      setCallMessage("");
      await loadAll();
    } catch (err) {
      setCallError(err instanceof ApiError ? err.message : "Couldn't place the call.");
    } finally {
      setCallBusy(false);
    }
  }

  async function handleBridgeCall() {
    if (!token || !fromNumber || !toNumber || !agentNumber) return;
    setCallBusy(true);
    setCallError(null);
    setCallSuccess(null);
    try {
      const customerNumber = toNumber;
      const ringingNumber = agentNumber;
      await placeBridgeCall(token, { to: toNumber, from: fromNumber, agent_number: agentNumber });
      setCallSuccess(
        `Calling ${ringingNumber} now — answer it to be connected live to ${customerNumber}.`
      );
      setToNumber("");
      await loadAll();
    } catch (err) {
      setCallError(err instanceof ApiError ? err.message : "Couldn't start the call.");
    } finally {
      setCallBusy(false);
    }
  }

  async function ensureVoiceDevice(): Promise<Device> {
    if (deviceRef.current) return deviceRef.current;
    if (!token) throw new Error("Not logged in.");
    const { token: voiceToken } = await getBrowserVoiceToken(token);
    const newDevice = new Device(voiceToken, { codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU] });
    newDevice.on("error", (err) => {
      setBrowserCallError(err.message || "A browser calling error occurred.");
      setBrowserCallStatus("error");
    });
    await newDevice.register();
    deviceRef.current = newDevice;
    return newDevice;
  }

  async function handleBrowserCall() {
    if (!fromNumber || !toNumber) return;
    setBrowserCallError(null);
    setBrowserCallStatus("connecting");
    try {
      const device = await ensureVoiceDevice();
      const call = await device.connect({ params: { To: toNumber, ZoikoFrom: fromNumber } });
      activeCallRef.current = call;
      setBrowserCallStatus("ringing");
      call.on("accept", () => setBrowserCallStatus("in-call"));
      call.on("disconnect", () => {
        setBrowserCallStatus("ended");
        activeCallRef.current = null;
      });
      call.on("cancel", () => {
        setBrowserCallStatus("ended");
        activeCallRef.current = null;
      });
      call.on("error", (err) => {
        setBrowserCallError(err.message || "The call ended with an error.");
        setBrowserCallStatus("error");
        activeCallRef.current = null;
      });
    } catch (err) {
      setBrowserCallError(err instanceof ApiError ? err.message : "Couldn't start the browser call.");
      setBrowserCallStatus("error");
    }
  }

  function handleHangUp() {
    activeCallRef.current?.disconnect();
  }

  useEffect(() => {
    return () => {
      activeCallRef.current?.disconnect();
      deviceRef.current?.destroy();
    };
  }, []);

  async function handleSummarize(kind: "call" | "voicemail", id: string) {
    if (!token) return;
    const key: SummaryKey = `${kind}:${id}`;
    setSummaries((prev) => ({ ...prev, [key]: { status: "busy" } }));
    try {
      const result = kind === "call" ? await summarizeCall(token, id) : await summarizeVoicemail(token, id);
      setSummaries((prev) => ({ ...prev, [key]: { status: "done", result } }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403 && err.message.toLowerCase().includes("consent")) {
        setSummaries((prev) => ({ ...prev, [key]: { status: "consent_required" } }));
        return;
      }
      const message = err instanceof ApiError ? err.message : "Couldn't generate a summary.";
      setSummaries((prev) => ({ ...prev, [key]: { status: "error", message } }));
    }
  }

  async function handleGrantConsent(kind: "call" | "voicemail", id: string) {
    if (!token) return;
    try {
      await grantAiProcessingConsent(token);
      await handleSummarize(kind, id);
    } catch {
      const key: SummaryKey = `${kind}:${id}`;
      setSummaries((prev) => ({ ...prev, [key]: { status: "error", message: "Couldn't grant AI consent." } }));
    }
  }

  const activeNumbers = numbers.filter((n) => n.status === "active");

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Calls</h2>
        <p className="text-sm text-slate-500">
          Inbound and outbound call logs, voicemail, and AI-generated summaries.
        </p>
      </div>

      {loadError && (
        <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{loadError}</p>
      )}

      {/* Make a call */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Make a call</h3>

        {activeNumbers.length === 0 && !loading ? (
          <p className="text-sm text-slate-500">
            You don&apos;t have an active number to call from yet — get one from Phone Numbers first.
          </p>
        ) : (
          <form onSubmit={handlePlaceCall} className="space-y-3">
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">From</label>
                <select
                  value={fromNumber}
                  onChange={(e) => setFromNumber(e.target.value)}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-mono text-slate-900"
                >
                  {activeNumbers.map((n) => (
                    <option key={n.id} value={n.e164}>
                      {n.e164}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">To</label>
                <input
                  type="tel"
                  required
                  value={toNumber}
                  onChange={(e) => setToNumber(e.target.value)}
                  placeholder="+15551234567"
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-mono text-slate-900 placeholder:text-slate-400"
                />
              </div>
              <button
                type="submit"
                disabled={callBusy || !fromNumber}
                className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
              >
                {callBusy ? "Calling..." : "Announce"}
              </button>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">Ring this number first</label>
                <input
                  type="tel"
                  value={agentNumber}
                  onChange={(e) => setAgentNumber(e.target.value)}
                  placeholder="+15559876543 (your real phone)"
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-mono text-slate-900 placeholder:text-slate-400"
                />
              </div>
              <button
                type="button"
                onClick={handleBridgeCall}
                disabled={callBusy || !fromNumber || !toNumber || !agentNumber}
                className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
                title="Rings the number above first, then connects you live to the To number once you answer"
              >
                {callBusy ? "Calling..." : "Talk Live"}
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {browserCallStatus === "in-call" || browserCallStatus === "ringing" || browserCallStatus === "connecting" ? (
                <button
                  type="button"
                  onClick={handleHangUp}
                  className="bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg px-4 py-2"
                >
                  Hang Up
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleBrowserCall}
                  disabled={!fromNumber || !toNumber}
                  className="bg-violet-600 hover:bg-violet-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
                  title="Talk directly through your computer's microphone - no phone needed at all"
                >
                  Call from Browser
                </button>
              )}
              {browserCallStatus === "connecting" && (
                <span className="text-sm text-slate-500">Connecting…</span>
              )}
              {browserCallStatus === "ringing" && (
                <span className="text-sm text-slate-500">Ringing {toNumber}…</span>
              )}
              {browserCallStatus === "in-call" && (
                <span className="text-sm text-emerald-600 font-medium">● Live — talking to {toNumber}</span>
              )}
              {browserCallStatus === "ended" && (
                <span className="text-sm text-slate-500">Call ended.</span>
              )}
              {browserCallStatus === "error" && browserCallError && (
                <span className="text-sm text-red-600">{browserCallError}</span>
              )}
            </div>
            <p className="text-xs text-slate-500">
              <strong>Announce</strong> plays a one-way message and hangs up. <strong>Talk Live</strong> rings a real phone first, then connects it live to the "To" number. <strong>Call from Browser</strong> uses your computer&apos;s microphone directly — no phone needed at all, talk right here in the tab.
            </p>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">
                Message for Announce (spoken aloud when they answer — not used by Talk Live)
              </label>
              <input
                type="text"
                value={callMessage}
                onChange={(e) => setCallMessage(e.target.value)}
                placeholder="This is a call from Zoiko Local."
                maxLength={500}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400"
              />
            </div>
          </form>
        )}

        {callError && <p className="text-sm text-red-600">{callError}</p>}
        {callSuccess && <p className="text-sm text-emerald-600 bg-emerald-50 rounded-lg px-3 py-2">{callSuccess}</p>}
      </div>

      {/* Call log */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Call log</h3>

        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {!loading && calls.length === 0 && <p className="text-sm text-slate-500">No calls yet.</p>}

        <div className="space-y-3">
          {calls.map((c) => (
            <CallRow
              key={c.id}
              label={`${displayNumber(c.direction === "inbound" ? c.from : c.to)} · ${c.direction}`}
              status={c.status}
              duration={c.duration}
              createdAt={c.created_at}
              recordingUrl={c.recording_url}
              suspectedSpam={c.is_suspected_spam}
              summaryState={summaries[`call:${c.id}`] ?? { status: "idle" }}
              onSummarize={() => handleSummarize("call", c.id)}
              onGrantConsent={() => handleGrantConsent("call", c.id)}
            />
          ))}
        </div>
      </div>

      {/* Voicemail */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Voicemail</h3>

        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {!loading && voicemails.length === 0 && <p className="text-sm text-slate-500">No voicemails yet.</p>}

        <div className="space-y-3">
          {voicemails.map((v) => (
            <CallRow
              key={v.id}
              label={`From ${displayNumber(v.from)}`}
              status="left a message"
              duration={v.duration}
              createdAt={v.created_at}
              recordingUrl={v.recording_url}
              summaryState={summaries[`voicemail:${v.id}`] ?? { status: "idle" }}
              onSummarize={() => handleSummarize("voicemail", v.id)}
              onGrantConsent={() => handleGrantConsent("voicemail", v.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
