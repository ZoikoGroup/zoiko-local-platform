"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { getBootstrap, moduleForPath, type Bootstrap } from "@/lib/platformAssistant";
import PlatformAssistantDrawer from "./PlatformAssistantDrawer";

const FALLBACK_BOOTSTRAP: Bootstrap = {
  enabled: true,
  assistant_name: "Zoiko Local Assistant",
  ai_disclosure: "I'm an automated Zoiko Local assistant. Read-only.",
  read_only_notice:
    "I can explain your setup and summarize information you're allowed to view. Changes happen in the secure workspace.",
  content_version: "offline-fallback",
  supported_modules: [
    { key: "overview", title: "Workspace overview", description: "Read-only guidance across your authenticated Zoiko Local workspace.", prompts: ["What can you see?", "Where do I manage numbers?"], destination_key: "DASHBOARD_HOME" },
    { key: "calls", title: "Calls", description: "Call history and recaps you are allowed to view.", prompts: ["Summarize recent calls", "Where are AI recaps?"], destination_key: "DASHBOARD_CALLS" },
    { key: "receptionist", title: "AI Receptionist", description: "Receptionist activity and Call Flows configuration guidance.", prompts: ["How did the receptionist handle calls?", "Where are call flows?"], destination_key: "DASHBOARD_CALL_FLOWS" },
    { key: "numbers", title: "Numbers", description: "Number portfolio, acquisition, assignment, and porting guidance.", prompts: ["Which numbers are on my account?", "Where do I manage numbers?"], destination_key: "DASHBOARD_NUMBERS" },
    { key: "porting", title: "Porting", description: "Porting request status and next steps.", prompts: ["What porting requests exist?", "Where do I track a port?"], destination_key: "DASHBOARD_NUMBERS" },
    { key: "billing", title: "Billing", description: "Entitlements, usage, and invoice explanation.", prompts: ["What is included in my plan?", "Where are invoices?"], destination_key: "DASHBOARD_BILLING" },
    { key: "inbox", title: "Inbox", description: "Conversations and voicemail guidance.", prompts: ["Do I have new voicemails?", "Where is the shared inbox?"], destination_key: "DASHBOARD_MESSAGING" },
    { key: "analytics", title: "Analytics", description: "Permissioned operational overview, reports, and quality insight.", prompts: ["Give me an operations overview", "Where are reports?"], destination_key: "DASHBOARD_REPORTS" },
    { key: "acquisition", title: "Number acquisition", description: "Supported-country registry and secure acquisition guidance.", prompts: ["Which countries are supported?", "How do I find a new number?"], destination_key: "DASHBOARD_NUMBERS" },
    { key: "video", title: "Video", description: "Video room and session metadata you are allowed to view.", prompts: ["What video sessions exist?", "Where are video meetings?"], destination_key: "DASHBOARD_VIDEO" },
    { key: "compliance", title: "Compliance", description: "Consent and retention posture you are allowed to view; no policy changes in chat.", prompts: ["What is my consent status?", "Where are retention policies?"], destination_key: "DASHBOARD_COMPLIANCE" },
    { key: "support", title: "Support and status", description: "Current incidents and support navigation.", prompts: ["Are there active incidents?", "Where do I get help?"], destination_key: "DASHBOARD_SUPPORT" },
  ],
};

export default function PlatformAssistantLauncher() {
  const pathname = usePathname();
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let active = true;
    getBootstrap()
      .then((value) => {
        if (active) setBootstrap(value.enabled ? value : null);
      })
      .catch(() => {
        if (active) setBootstrap(FALLBACK_BOOTSTRAP);
      });
    return () => {
      active = false;
    };
  }, []);

  const close = useCallback(() => setOpen(false), []);

  if (!bootstrap) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Open ${bootstrap.assistant_name}`}
        aria-haspopup="dialog"
        aria-controls="platform-assistant-panel"
        title={`Open ${bootstrap.assistant_name}`}
        className="fixed bottom-5 right-5 z-40 flex min-h-[44px] items-center gap-2 rounded-full bg-slate-900 px-3 py-1.5 text-sm font-medium text-white shadow-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2"
      >
        <span className="relative inline-flex">
          <Image src="/logo-icon.svg" alt="" width={28} height={28} unoptimized className="h-7 w-7 rounded-lg bg-white p-0.5" />
          <span className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-white text-slate-900 shadow">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M4 5h16v11H9l-5 3V5Z" />
            </svg>
          </span>
        </span>
        <span>Assistant</span>
      </button>
      {open && (
        <PlatformAssistantDrawer
          bootstrap={bootstrap}
          module={moduleForPath(pathname)}
          onClose={close}
        />
      )}
    </>
  );
}
