import { ApiError } from "@/lib/api";
import { getToken } from "@/lib/auth";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type PlatformModule =
  | "overview"
  | "calls"
  | "receptionist"
  | "numbers"
  | "porting"
  | "billing"
  | "inbox"
  | "analytics"
  | "acquisition"
  | "video"
  | "compliance"
  | "support";

export type ModuleInfo = {
  key: PlatformModule;
  title: string;
  description: string;
  prompts: string[];
  destination_key: string;
};

export type Bootstrap = {
  enabled: boolean;
  assistant_name: string;
  ai_disclosure: string;
  read_only_notice: string;
  content_version: string;
  supported_modules: ModuleInfo[];
};

export type SessionContext = {
  module: PlatformModule;
  role: string;
  tenant_scope: string;
  workspace_label: string;
  context_key: string | null;
  expires_at: string;
};

export type Session = {
  session_id: string;
  session_token: string;
  expires_at: string;
  context: SessionContext;
};

export type ContentBlock = {
  type: "paragraph" | "bullets";
  text: string | null;
  items: string[];
};

export type EvidenceItem = {
  source_id: string;
  title: string;
  retrieved_at: string | null;
  masked: boolean;
};

export type NavigationAction = {
  kind: "navigate";
  destination_key: string;
  label: string;
  href: string;
  read_only: boolean;
};

export type AssistantMessage = {
  message_id: string;
  client_message_id: string | null;
  request_id: string;
  answer_type: string;
  tenant_scope: string;
  context: SessionContext;
  content_blocks: ContentBlock[];
  evidence: EvidenceItem[];
  available_actions: NavigationAction[];
  uncertainty: string;
  risk_tier: string;
  read_only: boolean;
  content_version: string;
};

export type TranscriptEntry = {
  id: string;
  role: "user" | "assistant";
  text: string;
  message?: AssistantMessage;
};

export class PlatformAssistantError extends ApiError {}

const SESSION_ERROR_DETAILS = new Set([
  "Session expired - create a new session.",
  "Session not found or already ended.",
]);

export function isAssistantSessionError(error: unknown): boolean {
  return (
    error instanceof PlatformAssistantError &&
    error.status === 401 &&
    SESSION_ERROR_DETAILS.has(error.message)
  );
}

export function isSignInError(error: unknown): boolean {
  return (
    error instanceof PlatformAssistantError &&
    error.status === 401 &&
    !isAssistantSessionError(error)
  );
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  if (!token) {
    throw new PlatformAssistantError("Sign in to continue.", 401);
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : "Request failed";
    throw new PlatformAssistantError(message, response.status);
  }
  return response.json();
}

function sessionHeaders(session: Session): Record<string, string> {
  return {
    "X-Platform-Assistant-Session": session.session_id,
    "X-Platform-Assistant-Token": session.session_token,
  };
}

export function getBootstrap(): Promise<Bootstrap> {
  return request<Bootstrap>("/api/v1/platform-assistant/bootstrap");
}

export function createSession(module: PlatformModule, contextKey?: string): Promise<Session> {
  return request<Session>("/api/v1/platform-assistant/sessions", {
    method: "POST",
    body: JSON.stringify({ module, context_key: contextKey ?? null }),
  });
}

export function sendMessage(session: Session, text: string, intent?: string): Promise<AssistantMessage> {
  return request<AssistantMessage>("/api/v1/platform-assistant/messages", {
    method: "POST",
    headers: sessionHeaders(session),
    body: JSON.stringify({ text, intent: intent ?? null }),
  });
}

export function endSession(session: Session): Promise<{ ended: boolean; session_id: string }> {
  return request<{ ended: boolean; session_id: string }>("/api/v1/platform-assistant/sessions/end", {
    method: "POST",
    headers: sessionHeaders(session),
  });
}

export function sendFeedback(session: Session, messageId: string, rating: "helpful" | "not_helpful"): Promise<{ recorded: boolean }> {
  return request<{ recorded: boolean }>("/api/v1/platform-assistant/feedback", {
    method: "POST",
    headers: sessionHeaders(session),
    body: JSON.stringify({ message_id: messageId, rating }),
  });
}

export function moduleForPath(pathname: string): PlatformModule {
  const segments = pathname.split("/").filter(Boolean);
  const page = segments[1] ?? "";
  const mapping: Record<string, PlatformModule> = {
    calls: "calls",
    "call-flows": "receptionist",
    numbers: "numbers",
    billing: "billing",
    messaging: "inbox",
    voicemail: "inbox",
    "ai-insights": "analytics",
    reports: "analytics",
    video: "video",
    compliance: "compliance",
    support: "support",
  };
  return mapping[page] ?? "overview";
}
