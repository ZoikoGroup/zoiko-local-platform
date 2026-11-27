"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  createSession,
  endSession,
  isAssistantSessionError,
  isSignInError,
  PlatformAssistantError,
  sendFeedback,
  sendMessage,
  type AssistantMessage,
  type Bootstrap,
  type PlatformModule,
  type Session,
  type TranscriptEntry,
} from "@/lib/platformAssistant";
import AnswerCard from "./AnswerCard";
import ContextChip from "./ContextChip";
import DisclosureTray from "./DisclosureTray";

type Props = {
  bootstrap: Bootstrap;
  module: PlatformModule;
  onClose: () => void;
};

function ChatIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 5h16v11H9l-5 3V5Z" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m4 12 16-8-6 16-2.5-6.5L4 12Z" />
    </svg>
  );
}

export default function PlatformAssistantDrawer({ bootstrap, module, onClose }: Props) {
  const [session, setSession] = useState<Session | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [signInRequired, setSignInRequired] = useState(false);
  const [feedbackPending, setFeedbackPending] = useState<Record<string, boolean>>({});
  const closeRef = useRef<HTMLButtonElement>(null);
  const sessionRef = useRef<Session | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  const updateSession = useCallback((next: Session | null) => {
    sessionRef.current = next;
    setSession(next);
  }, []);

  useEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      const current = sessionRef.current;
      if (current) void endSession(current).catch(() => undefined);
      previousFocusRef.current?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    createSession(module)
      .then((next) => {
        if (!cancelled) updateSession(next);
      })
      .catch(() => {
        if (!cancelled) setError("The assistant could not start a session. Close and try again.");
      });
    return () => {
      cancelled = true;
    };
  }, [module, updateSession]);

  const submit = useCallback(
    async (text: string, intent?: string) => {
      const value = text.trim();
      if (!value || sending) return;
      setInput("");
      setError(null);
      setSignInRequired(false);
      setSending(true);
      const userEntry: TranscriptEntry = {
        id: `user-${Date.now()}`,
        role: "user",
        text: value,
      };
      setTranscript((current) => [...current, userEntry]);
      try {
        let activeSession = sessionRef.current;
        const expiresAt = activeSession ? Date.parse(activeSession.expires_at) : Number.NaN;
        if (!activeSession || Number.isNaN(expiresAt) || expiresAt <= Date.now() + 5_000) {
          activeSession = await createSession(module);
          updateSession(activeSession);
        }
        const sendWithRenewal = async (sessionToUse: Session): Promise<AssistantMessage> => {
          try {
            return await sendMessage(sessionToUse, value, intent);
          } catch (err) {
            const needsRenewal =
              isAssistantSessionError(err) ||
              (err instanceof PlatformAssistantError && err.status === 409);
            if (!needsRenewal) throw err;
            const fresh = await createSession(module);
            updateSession(fresh);
            return await sendMessage(fresh, value, intent);
          }
        };
        const answer = await sendWithRenewal(activeSession);
        setTranscript((current) => [
          ...current,
          { id: answer.message_id, role: "assistant", text: answer.content_blocks.map((block) => block.text ?? block.items.join(" ")).join(" "), message: answer },
        ]);
      } catch (err) {
        if (isSignInError(err)) {
          updateSession(null);
          setSignInRequired(true);
          setError("Your sign-in has expired. Sign in again to continue safely.");
        } else if (isAssistantSessionError(err) || (err instanceof PlatformAssistantError && err.status === 409)) {
          updateSession(null);
          setError("The assistant session could not be restarted. Close the panel and try again.");
        } else {
          setError("I can't verify that from an available source right now.");
        }
      } finally {
        setSending(false);
      }
    },
    [module, sending, updateSession],
  );

  const handleFeedback = useCallback(
    async (messageId: string, rating: "helpful" | "not_helpful") => {
      const activeSession = sessionRef.current;
      if (!activeSession) return;
      setFeedbackPending((current) => ({ ...current, [messageId]: true }));
      try {
        await sendFeedback(activeSession, messageId, rating);
      } catch {
        setError("Feedback could not be recorded.");
      } finally {
        setFeedbackPending((current) => ({ ...current, [messageId]: false }));
      }
    },
    [],
  );

  const moduleInfo = bootstrap.supported_modules.find((item) => item.key === module);

  return (
    <div className="fixed inset-0 z-40" role="presentation">
      <button type="button" className="absolute inset-0 h-full w-full bg-slate-900/30" aria-label="Close assistant" onClick={onClose} />
      <section
        id="platform-assistant-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`${bootstrap.assistant_name} for ${moduleInfo?.title ?? module}`}
        className="absolute inset-y-0 right-0 flex w-full max-w-[420px] flex-col bg-slate-50 shadow-2xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white p-4">
          <div className="flex items-center gap-3">
            <Image src="/logo-icon.svg" alt="" width={36} height={36} unoptimized className="h-9 w-9 rounded-xl" />
            <div>
              <h2 className="text-base font-semibold text-slate-900">{bootstrap.assistant_name}</h2>
              <p className="mt-1 text-xs text-slate-600">{moduleInfo?.title ?? module}</p>
            </div>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close assistant"
            className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border border-slate-300 text-slate-700"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>
        <div className="space-y-3 border-b border-slate-200 bg-white p-4">
          <ContextChip
            module={moduleInfo?.title ?? module}
            role={session?.context.role ?? "loading"}
            tenantScope={session?.context.tenant_scope ?? "server-derived"}
          />
          <DisclosureTray disclosure={bootstrap.ai_disclosure} notice={bootstrap.read_only_notice} />
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
          {transcript.length === 0 && (
            <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-700">
              <p className="font-medium">How can I help with your Zoiko Local workspace?</p>
              <p className="mt-2 text-xs text-slate-600">{bootstrap.read_only_notice}</p>
              {(moduleInfo?.prompts ?? []).length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2" aria-label="Suggested questions">
                  {(moduleInfo?.prompts ?? []).map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => void submit(prompt)}
                      className="inline-flex min-h-[44px] items-center gap-1.5 rounded-full border border-slate-300 px-3 text-xs text-slate-700 transition-colors hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
                    >
                      <ChatIcon />
                      {prompt}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {transcript.map((entry) =>
            entry.role === "user" ? (
              <div key={entry.id} className="ml-auto max-w-[85%] rounded-xl bg-slate-900 px-3 py-2 text-sm text-white">
                {entry.text}
              </div>
            ) : entry.message ? (
              <AnswerCard
                key={entry.id}
                message={entry.message}
                feedbackDisabled={Boolean(feedbackPending[entry.id])}
                onFeedback={handleFeedback}
              />
            ) : null,
          )}
          {error && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              <p>{error}</p>
              {signInRequired && (
                <Link href="/login" className="mt-2 inline-block font-medium underline">
                  Go to sign in
                </Link>
              )}
            </div>
          )}
        </div>
        <form
          className="border-t border-slate-200 bg-white p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit(input);
          }}
        >
          <div className="flex gap-2">
            <label htmlFor="platform-assistant-input" className="sr-only">
              Message the assistant
            </label>
            <input
              id="platform-assistant-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              maxLength={1000}
              placeholder="Ask about your workspace"
              className="min-h-[44px] flex-1 rounded-md border border-slate-300 px-3 text-sm"
            />
            <button
              type="submit"
              disabled={sending || !session || input.trim().length === 0}
              className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-slate-900 px-4 text-sm font-medium text-white disabled:opacity-50"
            >
              <SendIcon />
              Send
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
