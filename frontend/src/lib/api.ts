const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type User = {
  id: string;
  email: string;
  role: string;
  account_id: string;
  mfa_enabled: boolean;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.detail ?? "Request failed", response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

export function signup(input: {
  account_name: string;
  account_type: "individual" | "business";
  email: string;
  password: string;
}): Promise<User> {
  return request<User>("/auth/signup", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export type LoginResult = {
  mfa_required: boolean;
  access_token: string | null;
  token_type: string;
  mfa_token: string | null;
};

export function login(input: { email: string; password: string }): Promise<LoginResult> {
  return request("/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getCurrentUser(token: string): Promise<User> {
  return request<User>("/auth/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function googleAuth(credential: string): Promise<{ access_token: string; token_type: string }> {
  return request("/auth/google", {
    method: "POST",
    body: JSON.stringify({ credential }),
  });
}

export function completeMfaLogin(
  mfaToken: string,
  code: string
): Promise<{ access_token: string; token_type: string }> {
  return request("/auth/mfa/login", {
    method: "POST",
    body: JSON.stringify({ mfa_token: mfaToken, code }),
  });
}

export function mfaSetup(token: string): Promise<{ secret: string; otpauth_uri: string }> {
  return request("/auth/mfa/setup", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function mfaEnable(token: string, code: string): Promise<void> {
  return request("/auth/mfa/enable", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ code }),
  });
}

export function mfaDisable(token: string, code: string): Promise<void> {
  return request("/auth/mfa/disable", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ code }),
  });
}

export type ComplianceRule = {
  id: string;
  country: string;
  requirement_type: string;
  required_documents: string[];
  is_active: boolean;
};

export function getComplianceRules(country: string): Promise<ComplianceRule[]> {
  return request<ComplianceRule[]>(`/compliance/rules?country=${country}`);
}

export type ComplianceCase = {
  id: string;
  status: string;
  jurisdiction: string;
  requirement_type: string;
};

export function openComplianceCase(
  token: string,
  input: { jurisdiction: string; requirement_type: string }
): Promise<ComplianceCase> {
  return request<ComplianceCase>("/compliance/cases", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export type MyComplianceCase = ComplianceCase & {
  documents: { document_type: string; reference: string }[];
  created_at: string;
};

export function listMyComplianceCases(token: string): Promise<MyComplianceCase[]> {
  return request<MyComplianceCase[]>("/compliance/cases/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- Staff / Admin console ---

export function staffLogin(input: {
  email: string;
  password: string;
}): Promise<{ access_token: string; token_type: string }> {
  return request("/staff/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export type StaffComplianceCase = ComplianceCase & {
  account_id: string;
  account_name: string;
  account_owner_email: string;
  number_id: string | null;
  documents: { document_type: string; reference: string }[];
  expires_at: string | null;
  created_at: string;
};

export function listStaffCases(
  token: string,
  status?: string
): Promise<StaffComplianceCase[]> {
  const query = status ? `?status=${status}` : "";
  return request<StaffComplianceCase[]>(`/compliance/cases${query}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function staffApproveCase(token: string, caseId: string): Promise<ComplianceCase> {
  return request<ComplianceCase>(`/compliance/cases/${caseId}/approve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function staffRejectCase(
  token: string,
  caseId: string,
  reason?: string
): Promise<ComplianceCase> {
  return request<ComplianceCase>(`/compliance/cases/${caseId}/reject`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ reason }),
  });
}

export type AccountOverview = {
  id: string;
  name: string;
  account_type: string;
  owner_email: string | null;
  member_count: number;
  number_count: number;
  created_at: string;
};

export function listStaffAccounts(token: string): Promise<AccountOverview[]> {
  return request<AccountOverview[]>("/staff/accounts", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type AuditEvent = {
  id: string;
  actor: string;
  action: string;
  target: string;
  before_hash: string | null;
  after_hash: string | null;
  reason: string | null;
  correlation_id: string | null;
  created_at: string;
};

export function listStaffAuditEvents(token: string): Promise<AuditEvent[]> {
  return request<AuditEvent[]>("/audit/events", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- Numbers ---

export type MyPhoneNumber = {
  id: string;
  e164: string;
  country: string;
  status: string;
  assigned_user_id: string | null;
  reserved_until: string | null;
  forwarding_number: string | null;
  ai_receptionist_enabled: boolean;
};

export function listMyNumbers(token: string): Promise<MyPhoneNumber[]> {
  return request<MyPhoneNumber[]>("/numbers", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type NumberSearchResult = {
  phone_number: string;
  locality: string | null;
  region: string | null;
  capabilities: Record<string, boolean> | null;
};

export function searchNumbers(
  token: string,
  input: { country: string; number_type?: string; area_code?: string }
): Promise<NumberSearchResult[]> {
  const params = new URLSearchParams({ country: input.country });
  if (input.number_type) params.set("number_type", input.number_type);
  if (input.area_code) params.set("area_code", input.area_code);
  return request<NumberSearchResult[]>(`/numbers/search?${params.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function reserveNumber(
  token: string,
  input: { e164: string; country: string }
): Promise<MyPhoneNumber> {
  return request<MyPhoneNumber>("/numbers/reserve", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function purchaseNumber(token: string, e164: string): Promise<MyPhoneNumber> {
  return request<MyPhoneNumber>("/numbers/purchase", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ e164 }),
  });
}

export function suspendNumber(token: string, e164: string): Promise<MyPhoneNumber> {
  return request<MyPhoneNumber>(`/numbers/${encodeURIComponent(e164)}/suspend`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function cancelNumber(token: string, e164: string): Promise<MyPhoneNumber> {
  return request<MyPhoneNumber>(`/numbers/${encodeURIComponent(e164)}/cancel`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type CallLogEntry = {
  id: string;
  sid: string | null;
  status: string;
  to: string;
  from: string;
  direction: "inbound" | "outbound";
  duration: number | null;
  recording_url: string | null;
  created_at: string;
};

export function listCalls(token: string): Promise<CallLogEntry[]> {
  return request<CallLogEntry[]>("/media/voice/calls", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function placeOutboundCall(
  token: string,
  input: { to: string; from: string; message?: string }
): Promise<{ sid: string; status: string; to: string; from: string }> {
  return request("/media/voice/outbound", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export type VoicemailEntry = {
  id: string;
  from: string;
  recording_url: string;
  duration: number | null;
  created_at: string;
};

export function listVoicemails(token: string): Promise<VoicemailEntry[]> {
  return request<VoicemailEntry[]>("/media/voicemail", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type ConversationSummary = {
  id: string;
  source_type: string;
  transcript: string;
  summary: string;
  model_version: string;
  disclaimer: string;
};

export function summarizeCall(token: string, callId: string): Promise<ConversationSummary> {
  return request<ConversationSummary>(`/intelligence/calls/${callId}/summarize`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function summarizeVoicemail(token: string, voicemailId: string): Promise<ConversationSummary> {
  return request<ConversationSummary>(`/intelligence/voicemails/${voicemailId}/summarize`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function grantAiProcessingConsent(token: string): Promise<{ granted_at: string }> {
  return request("/compliance/consent", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ consent_type: "ai_processing" }),
  });
}

// --- Video ---

export type VideoRoom = {
  room_name: string;
  status: "created" | "active" | "ended";
  started_at: string | null;
  ended_at: string | null;
  recording_in_progress: boolean;
  recording_url: string | null;
};

export function listVideoRooms(token: string): Promise<VideoRoom[]> {
  return request<VideoRoom[]>("/media/video/rooms", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function createVideoRoom(token: string): Promise<{ room_name: string; status: string }> {
  return request("/media/video/rooms", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function joinVideoRoom(
  token: string,
  roomName: string,
  displayName: string
): Promise<{ token: string; url: string }> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/token`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ display_name: displayName }),
  });
}

export function endVideoRoom(token: string, roomName: string): Promise<{ room_name: string; status: string }> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/end`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function startVideoRecording(
  token: string,
  roomName: string
): Promise<{ room_name: string; recording: boolean }> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/recording/start`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}
