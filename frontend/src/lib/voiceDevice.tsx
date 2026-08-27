"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Call, Device } from "@twilio/voice-sdk";
import { getBrowserVoiceToken, ApiError } from "@/lib/api";
import { getToken } from "@/lib/auth";

export type VoiceCallStatus =
  | "idle"
  | "connecting"
  | "ringing"
  | "incoming"
  | "in-call"
  | "ended"
  | "error";

interface VoiceDeviceContextValue {
  status: VoiceCallStatus;
  error: string | null;
  incomingFrom: string | null;
  activeCallTo: string | null;
  placeCall: (to: string, from: string) => Promise<void>;
  acceptIncoming: () => void;
  rejectIncoming: () => void;
  hangUp: () => void;
}

const VoiceDeviceContext = createContext<VoiceDeviceContextValue | null>(null);

/**
 * One Twilio Voice Device shared across the whole dashboard, not just the
 * Calls page - registered as soon as the dashboard mounts so an inbound
 * call (media/voice.py rings "client:<account_id>" alongside the number's
 * real phone destinations) can reach the browser no matter which page is
 * open. VoiceCallOverlay (rendered once in dashboard/layout.tsx) surfaces
 * the incoming-call Accept/Reject UI; the Calls page's own "Call from
 * Browser" button reuses this same context for outbound calls instead of
 * running a second, separate Device registration.
 */
export function VoiceDeviceProvider({ children }: { children: ReactNode }) {
  const deviceRef = useRef<Device | null>(null);
  const activeCallRef = useRef<Call | null>(null);
  const incomingCallRef = useRef<Call | null>(null);

  const [status, setStatus] = useState<VoiceCallStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [incomingFrom, setIncomingFrom] = useState<string | null>(null);
  const [activeCallTo, setActiveCallTo] = useState<string | null>(null);

  const attachActiveCall = useCallback((call: Call, to: string | null) => {
    activeCallRef.current = call;
    setActiveCallTo(to);
    call.on("accept", () => setStatus("in-call"));
    call.on("disconnect", () => {
      activeCallRef.current = null;
      incomingCallRef.current = null;
      setActiveCallTo(null);
      setIncomingFrom(null);
      setStatus("ended");
    });
    call.on("reject", () => {
      activeCallRef.current = null;
      incomingCallRef.current = null;
      setActiveCallTo(null);
      setIncomingFrom(null);
      setStatus("idle");
    });
    call.on("error", (err) => {
      setError(err.message || "The call ended with an error.");
      activeCallRef.current = null;
      incomingCallRef.current = null;
      setActiveCallTo(null);
      setIncomingFrom(null);
      setStatus("error");
    });
  }, []);

  // Twilio Access Tokens carry a fixed lifetime (build_voice_access_token
  // issues them with ttl=3600, 1 hour) - a Device kept alive for the whole
  // dashboard session (see this provider's own docstring) will otherwise
  // hit a dead token well before the tab closes. AccessTokenInvalid (20101)
  // and AccessTokenExpired (20104) are the two codes Twilio actually raises
  // for this; treated as recoverable by tearing the Device down so the next
  // reconnect attempt mints a fresh token, rather than leaving the banner
  // stuck showing a permanent error until the page is manually refreshed.
  const RECOVERABLE_ERROR_CODES = new Set([20101, 20104]);
  const reconnectingRef = useRef(false);

  const ensureDeviceRef = useRef<(() => Promise<Device>) | null>(null);

  const scheduleReconnect = useCallback(() => {
    if (reconnectingRef.current) return;
    reconnectingRef.current = true;
    setTimeout(() => {
      reconnectingRef.current = false;
      ensureDeviceRef.current?.().catch((err) => {
        setError(err instanceof Error ? err.message : "Couldn't reconnect browser calling.");
      });
    }, 2000);
  }, []);

  const ensureDevice = useCallback(async (): Promise<Device> => {
    if (deviceRef.current) return deviceRef.current;
    const token = getToken();
    if (!token) throw new Error("Not logged in.");
    const { token: voiceToken } = await getBrowserVoiceToken(token);
    const newDevice = new Device(voiceToken, {
      codecPreferences: [Call.Codec.Opus, Call.Codec.PCMU],
    });
    newDevice.on("tokenWillExpire", async () => {
      try {
        const authToken = getToken();
        if (!authToken) return;
        const { token: freshVoiceToken } = await getBrowserVoiceToken(authToken);
        newDevice.updateToken(freshVoiceToken);
      } catch {
        // Best-effort - if the refresh itself fails, the token will
        // eventually expire for real and the "error" handler below
        // recovers by tearing down and reconnecting from scratch.
      }
    });
    newDevice.on("error", (err) => {
      setError(err.message || "A browser calling error occurred.");
      setStatus("error");
      if (RECOVERABLE_ERROR_CODES.has(err.code) && deviceRef.current === newDevice) {
        deviceRef.current = null;
        newDevice.destroy();
        scheduleReconnect();
      }
    });
    newDevice.on("incoming", (call) => {
      incomingCallRef.current = call;
      setError(null);
      setIncomingFrom(call.parameters.From || "Unknown caller");
      setStatus("incoming");
      // Fires only if the caller hangs up (or the other ring-group leg
      // answers first) before we accept/reject - once accepted,
      // attachActiveCall's own "disconnect" handler takes over.
      call.on("cancel", () => {
        incomingCallRef.current = null;
        setIncomingFrom(null);
        setStatus("idle");
      });
    });
    await newDevice.register();
    deviceRef.current = newDevice;
    return newDevice;
  }, [scheduleReconnect]);

  useEffect(() => {
    ensureDeviceRef.current = ensureDevice;
  }, [ensureDevice]);

  useEffect(() => {
    if (!getToken()) return;
    ensureDevice().catch((err) => {
      setError(err instanceof Error ? err.message : "Couldn't set up browser calling.");
    });
    return () => {
      activeCallRef.current?.disconnect();
      deviceRef.current?.destroy();
      deviceRef.current = null;
    };
  }, [ensureDevice]);

  const placeCall = useCallback(
    async (to: string, from: string) => {
      setError(null);
      setStatus("connecting");
      try {
        const device = await ensureDevice();
        const call = await device.connect({ params: { To: to, ZoikoFrom: from } });
        attachActiveCall(call, to);
        setStatus("ringing");
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Couldn't start the browser call."
        );
        setStatus("error");
      }
    },
    [ensureDevice, attachActiveCall]
  );

  const acceptIncoming = useCallback(() => {
    const call = incomingCallRef.current;
    if (!call) return;
    attachActiveCall(call, call.parameters.From || null);
    call.accept();
  }, [attachActiveCall]);

  const rejectIncoming = useCallback(() => {
    incomingCallRef.current?.reject();
    incomingCallRef.current = null;
    setIncomingFrom(null);
    setStatus("idle");
  }, []);

  const hangUp = useCallback(() => {
    activeCallRef.current?.disconnect();
  }, []);

  return (
    <VoiceDeviceContext.Provider
      value={{ status, error, incomingFrom, activeCallTo, placeCall, acceptIncoming, rejectIncoming, hangUp }}
    >
      {children}
    </VoiceDeviceContext.Provider>
  );
}

export function useVoiceDevice(): VoiceDeviceContextValue {
  const ctx = useContext(VoiceDeviceContext);
  if (!ctx) throw new Error("useVoiceDevice must be used within a VoiceDeviceProvider");
  return ctx;
}
