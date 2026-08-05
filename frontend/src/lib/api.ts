const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type User = {
  id: string;
  email: string;
  role: string;
  account_id: string;
  mfa_enabled: boolean;
  phone_number: string | null;
};

export type TeamMember = {
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

export function setPhoneNumber(token: string, phoneNumber: string | null): Promise<User> {
  return request<User>("/auth/me/phone", {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ phone_number: phoneNumber }),
  });
}

export function listTeamMembers(token: string): Promise<TeamMember[]> {
  return request<TeamMember[]>("/team/members", {
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
  kyc_inquiry_id: string | null;
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

export function startKycVerification(
  token: string,
  caseId: string
): Promise<{ inquiry_id: string; verification_url: string }> {
  return request(`/compliance/cases/${caseId}/kyc/start`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type NotificationDelivery = {
  id: string;
  event_name: string;
  channel: "email" | "sms" | "push";
  recipient_email: string | null;
  recipient_phone: string | null;
  subject: string;
  status: "sent" | "failed";
  error: string | null;
  created_at: string;
  read_at: string | null;
};

export function listMyNotifications(token: string): Promise<NotificationDelivery[]> {
  return request<NotificationDelivery[]>("/notifications/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function markNotificationRead(token: string, notificationId: string): Promise<NotificationDelivery> {
  return request<NotificationDelivery>(`/notifications/${notificationId}/read`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function markAllNotificationsRead(token: string): Promise<{ marked_read: number }> {
  return request(`/notifications/read-all`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type PushSubscriptionInfo = {
  id: string;
  endpoint: string;
  created_at: string;
};

export function subscribeToPush(
  token: string,
  input: { endpoint: string; p256dh: string; auth: string }
): Promise<PushSubscriptionInfo> {
  return request<PushSubscriptionInfo>("/notifications/push/subscribe", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function unsubscribeFromPush(token: string, endpoint: string): Promise<void> {
  return request<void>("/notifications/push/unsubscribe", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ endpoint }),
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

export type StaffNumberSearchResult = {
  id: string;
  e164: string;
  country: string;
  status: string;
  provider_sid: string | null;
  account_id: string;
  account_name: string | null;
  account_owner_email: string | null;
  created_at: string;
};

export function searchStaffNumbers(staffToken: string, q: string): Promise<StaffNumberSearchResult[]> {
  return request<StaffNumberSearchResult[]>(`/staff/numbers/search?q=${encodeURIComponent(q)}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export type StuckProvisioningEntry = {
  id: string;
  e164: string;
  country: string;
  status: string;
  account_id: string;
  account_name: string | null;
  account_owner_email: string | null;
  provisioning_started_at: string | null;
};

export function listStuckProvisioning(staffToken: string): Promise<StuckProvisioningEntry[]> {
  return request<StuckProvisioningEntry[]>("/staff/numbers/stuck-provisioning", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function retryProvisioning(
  staffToken: string,
  numberId: string
): Promise<{ id: string; e164: string; status: string }> {
  return request(`/staff/numbers/${numberId}/retry-provisioning`, {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function releaseProvisioning(
  staffToken: string,
  numberId: string
): Promise<{ id: string; e164: string; status: string }> {
  return request(`/staff/numbers/${numberId}/release-provisioning`, {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
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

// --- Provider status (staff console) ---

export type ProviderStatus = {
  name: string;
  configured: boolean;
  ok: boolean;
  detail: string | null;
};

export function listProviderStatuses(staffToken: string): Promise<{ providers: ProviderStatus[] }> {
  return request<{ providers: ProviderStatus[] }>("/ops/provider-status", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

// --- Public status page (no auth) ---

export type PublicStatus = {
  overall: "operational" | "degraded";
  components: { name: string; status: "operational" | "degraded" }[];
};

export function getPublicStatus(): Promise<PublicStatus> {
  return request<PublicStatus>("/ops/status");
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
  business_hours_start: string | null;
  business_hours_end: string | null;
  business_hours_timezone: string;
  ai_receptionist_enabled: boolean;
  escalation_user_id: string | null;
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

export function suspendNumber(token: string, e164: string, reason?: string): Promise<MyPhoneNumber> {
  return request<MyPhoneNumber>(`/numbers/${encodeURIComponent(e164)}/suspend`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ reason: reason || null }),
  });
}

export function cancelNumber(token: string, e164: string): Promise<MyPhoneNumber> {
  return request<MyPhoneNumber>(`/numbers/${encodeURIComponent(e164)}/cancel`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function configureRouting(
  token: string,
  e164: string,
  input: {
    forwarding_number?: string | null;
    business_hours_start?: string | null;
    business_hours_end?: string | null;
    business_hours_timezone?: string;
    ai_receptionist_enabled?: boolean;
    escalation_user_id?: string | null;
  }
): Promise<MyPhoneNumber> {
  return request<MyPhoneNumber>(`/numbers/${encodeURIComponent(e164)}/routing`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
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
  language: string | null;
  urgency: "low" | "medium" | "high" | null;
  action_items: string[];
  suggested_follow_up: string | null;
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

export function summarizeVideoSession(token: string, roomName: string): Promise<ConversationSummary> {
  return request<ConversationSummary>(`/intelligence/video-sessions/${encodeURIComponent(roomName)}/summarize`, {
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

export function acknowledgeEmergencyCallingLimitation(token: string): Promise<{ granted_at: string }> {
  return request("/compliance/consent", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ consent_type: "emergency_calling_acknowledged" }),
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
  participant_minutes: number;
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

// --- Data retention ---

export type RetentionPolicies = {
  voicemail: number;
  call_recording: number;
  video_recording: number;
};

export function listRetentionPolicies(token: string): Promise<RetentionPolicies> {
  return request<RetentionPolicies>("/retention/policies", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- Account audit log (Owner/Admin only, scoped to the caller's own account) ---

export function listMyAuditEvents(token: string): Promise<AuditEvent[]> {
  return request<AuditEvent[]>("/audit/events/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function setRetentionPolicy(
  token: string,
  artifactType: keyof RetentionPolicies,
  retentionDays: number
): Promise<{ artifact_type: string; retention_days: number }> {
  return request(`/retention/policies/${artifactType}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ retention_days: retentionDays }),
  });
}

// --- AI Insights ---

export type SummaryListEntry = {
  id: string;
  source_type: string;
  source_id: string;
  transcript: string;
  summary: string;
  language: string | null;
  urgency: "low" | "medium" | "high" | null;
  action_items: string[];
  suggested_follow_up: string | null;
  model_version: string;
  created_at: string;
  disclaimer: string;
  original_summary: string | null;
  edited_at: string | null;
  edited_by_user_id: string | null;
};

export function listSummaries(token: string): Promise<SummaryListEntry[]> {
  return request<SummaryListEntry[]>("/intelligence/summaries", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function searchSummaries(token: string, q: string): Promise<SummaryListEntry[]> {
  return request<SummaryListEntry[]>(`/intelligence/summaries/search?q=${encodeURIComponent(q)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function editSummary(token: string, summaryId: string, summary: string): Promise<SummaryListEntry> {
  return request<SummaryListEntry>(`/intelligence/summaries/${summaryId}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ summary }),
  });
}

export type ReceptionistCallEntry = {
  id: string;
  call_sid: string;
  caller_number: string;
  raw_transcript: string;
  caller_name: string | null;
  caller_company: string | null;
  reason: string | null;
  summary: string | null;
  urgency: "low" | "medium" | "high" | null;
  escalated: boolean;
  guardrail_flags: string[];
  assigned_user_id: string | null;
  assigned_user_email: string | null;
  original_summary: string | null;
  edited_at: string | null;
  model_version: string | null;
  created_at: string;
};

export function listReceptionistCalls(token: string): Promise<ReceptionistCallEntry[]> {
  return request<ReceptionistCallEntry[]>("/media/receptionist/calls", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function assignReceptionistCall(
  token: string,
  callId: string,
  assignedUserId: string | null
): Promise<{ id: string; assigned_user_id: string | null }> {
  return request(`/media/receptionist/calls/${callId}/assign`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ assigned_user_id: assignedUserId }),
  });
}

export function editReceptionistCallSummary(
  token: string,
  callId: string,
  summary: string
): Promise<{ id: string; summary: string; original_summary: string | null; edited_at: string | null }> {
  return request(`/media/receptionist/calls/${callId}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ summary }),
  });
}

// --- Usage (Owner/Admin only) ---

export type UsageEvent = {
  id: string;
  event_type: string;
  quantity: number;
  unit: string;
  country_band: string | null;
  created_at: string;
};

export function listUsage(token: string): Promise<UsageEvent[]> {
  return request<UsageEvent[]>("/usage", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- Number Porting ---

export type PortingRequest = {
  id: string;
  account_id: string;
  phone_number: string;
  country: string;
  current_carrier: string;
  carrier_account_number: string;
  billing_name: string;
  billing_address: string;
  status: "submitted" | "approved" | "rejected" | "completed" | "canceled";
  rejection_reason: string | null;
  twilio_incoming_number_sid: string | null;
  created_number_id: string | null;
  created_at: string;
};

export type StaffPortingRequest = PortingRequest & {
  account_name: string;
  account_owner_email: string;
};

export function createPortingRequest(
  token: string,
  input: {
    phone_number: string;
    country: string;
    current_carrier: string;
    carrier_account_number: string;
    billing_name: string;
    billing_address: string;
  }
): Promise<PortingRequest> {
  return request<PortingRequest>("/porting/requests", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function listMyPortingRequests(token: string): Promise<PortingRequest[]> {
  return request<PortingRequest[]>("/porting/requests/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function cancelPortingRequest(token: string, requestId: string): Promise<PortingRequest> {
  return request<PortingRequest>(`/porting/requests/${requestId}/cancel`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function listStaffPortingRequests(
  staffToken: string,
  status?: string
): Promise<StaffPortingRequest[]> {
  const query = status ? `?status=${status}` : "";
  return request<StaffPortingRequest[]>(`/porting/requests${query}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function staffApprovePortingRequest(staffToken: string, requestId: string): Promise<StaffPortingRequest> {
  return request<StaffPortingRequest>(`/porting/requests/${requestId}/approve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function staffRejectPortingRequest(
  staffToken: string,
  requestId: string,
  reason?: string
): Promise<StaffPortingRequest> {
  return request<StaffPortingRequest>(`/porting/requests/${requestId}/reject`, {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
    body: JSON.stringify({ reason }),
  });
}

export function staffCompletePortingRequest(
  staffToken: string,
  requestId: string,
  twilioIncomingNumberSid: string
): Promise<StaffPortingRequest> {
  return request<StaffPortingRequest>(`/porting/requests/${requestId}/complete`, {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
    body: JSON.stringify({ twilio_incoming_number_sid: twilioIncomingNumberSid }),
  });
}

// --- Risk / blocked destinations (staff console) ---

export type BlockedDestination = {
  id: string;
  prefix: string;
  reason: string;
  created_at: string;
};

export function listBlockedDestinations(staffToken: string): Promise<BlockedDestination[]> {
  return request<BlockedDestination[]>("/risk/blocked-destinations", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function addBlockedDestination(
  staffToken: string,
  input: { prefix: string; reason: string }
): Promise<BlockedDestination> {
  return request<BlockedDestination>("/risk/blocked-destinations", {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
    body: JSON.stringify(input),
  });
}

export function removeBlockedDestination(staffToken: string, ruleId: string): Promise<void> {
  return request<void>(`/risk/blocked-destinations/${encodeURIComponent(ruleId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

// --- Contacts ---

export type Contact = {
  id: string;
  name: string;
  phone_number: string;
  email: string | null;
  notes: string | null;
  created_at: string;
};

export type ContactHistoryEntry = {
  type: "call" | "voicemail" | "receptionist_call";
  id: string;
  direction: string | null;
  status: string | null;
  duration: number | null;
  summary: string | null;
  recording_url: string | null;
  created_at: string;
};

export function listContacts(token: string): Promise<Contact[]> {
  return request<Contact[]>("/contacts", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function createContact(
  token: string,
  input: { name: string; phone_number: string; email?: string; notes?: string }
): Promise<Contact> {
  return request<Contact>("/contacts", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function updateContact(
  token: string,
  contactId: string,
  input: { name: string; phone_number: string; email?: string; notes?: string }
): Promise<Contact> {
  return request<Contact>(`/contacts/${encodeURIComponent(contactId)}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function deleteContact(token: string, contactId: string): Promise<void> {
  return request<void>(`/contacts/${encodeURIComponent(contactId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function getContactHistory(token: string, contactId: string): Promise<ContactHistoryEntry[]> {
  return request<ContactHistoryEntry[]>(`/contacts/${encodeURIComponent(contactId)}/history`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}
