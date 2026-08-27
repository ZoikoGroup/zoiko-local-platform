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
  // FormData bodies (file uploads) must NOT get a Content-Type set here -
  // the browser generates its own multipart boundary, and overriding it
  // with application/json breaks the upload silently server-side.
  const isFormData = options.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
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

// Architecture doc §5 "Fraud and Risk: device fingerprinting" - a coarse,
// no-third-party-SDK client fingerprint (not a real anti-tampering
// fingerprint like FingerprintJS) built from a few stable, low-invasiveness
// signals. Sent as an optional header on signup, login, and placing a call
// (the three actions a device can take against an account); the backend
// never requires it and never blocks any of them on it (see backend
// app.risk.service.check_fingerprint_on_{signup,login,call}'s docstrings) -
// this is a detection signal, not an access gate.
export async function computeDeviceFingerprint(): Promise<string | null> {
  if (typeof window === "undefined" || !window.crypto?.subtle) return null;
  try {
    const raw = [
      navigator.userAgent,
      navigator.language,
      `${screen.width}x${screen.height}x${screen.colorDepth}`,
      Intl.DateTimeFormat().resolvedOptions().timeZone,
      String(navigator.hardwareConcurrency ?? ""),
    ].join("|");
    const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    return null;
  }
}

export async function signup(input: {
  account_name: string;
  account_type: "individual" | "business";
  email: string;
  password: string;
}): Promise<User> {
  const fingerprint = await computeDeviceFingerprint();
  return request<User>("/auth/signup", {
    method: "POST",
    headers: fingerprint ? { "X-Device-Fingerprint": fingerprint } : {},
    body: JSON.stringify(input),
  });
}

export type LoginResult = {
  mfa_required: boolean;
  access_token: string | null;
  token_type: string;
  mfa_token: string | null;
};

export async function login(input: { email: string; password: string }): Promise<LoginResult> {
  const fingerprint = await computeDeviceFingerprint();
  return request("/auth/login", {
    method: "POST",
    headers: fingerprint ? { "X-Device-Fingerprint": fingerprint } : {},
    body: JSON.stringify(input),
  });
}

export function forgotPassword(email: string): Promise<void> {
  return request<void>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, newPassword: string): Promise<LoginResult> {
  return request<LoginResult>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
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

export function addTeamMember(
  token: string,
  input: { email: string; password: string; role: "admin" | "member" | "viewer" }
): Promise<TeamMember> {
  return request<TeamMember>("/team/members", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function removeTeamMember(token: string, userId: string): Promise<void> {
  return request<void>(`/team/members/${encodeURIComponent(userId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function googleAuth(
  credential: string
): Promise<{ access_token: string; token_type: string; is_new_account: boolean }> {
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

export type ComplianceDocument = {
  document_type: string;
  storage_key: string;
  filename: string;
  content_type: string;
  uploaded_at: string;
};

export type MyComplianceCase = ComplianceCase & {
  documents: ComplianceDocument[];
  created_at: string;
};

export function listMyComplianceCases(token: string): Promise<MyComplianceCase[]> {
  return request<MyComplianceCase[]>("/compliance/cases/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function submitComplianceDocument(
  token: string,
  caseId: string,
  documentType: string,
  file: File
): Promise<MyComplianceCase> {
  const formData = new FormData();
  formData.append("document_type", documentType);
  formData.append("file", file);
  return request<MyComplianceCase>(`/compliance/cases/${caseId}/documents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
}

export function getComplianceDocumentDownloadUrl(
  token: string,
  caseId: string,
  documentIndex: number
): Promise<{ url: string }> {
  return request(`/compliance/cases/${caseId}/documents/${documentIndex}/download-url`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function getStaffComplianceDocumentDownloadUrl(
  staffToken: string,
  caseId: string,
  documentIndex: number
): Promise<{ url: string }> {
  return request(`/compliance/staff/cases/${caseId}/documents/${documentIndex}/download-url`, {
    headers: { Authorization: `Bearer ${staffToken}` },
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
  status: "sent" | "failed" | "suppressed";
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

// Public half of the backend's VAPID keypair - safe to expose client-side
// (that's the whole point of the VAPID public/private split). Blank when
// no keypair is configured yet, same "not configured" posture the rest of
// the Provider Gateway follows.
export function getVapidPublicKey(): string {
  return process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY ?? "";
}

export type NotificationPreferences = {
  transactional_enabled: boolean;
  sms_enabled: boolean;
  quiet_hours_start: string | null; // "HH:MM:SS"
  quiet_hours_end: string | null;
  quiet_hours_timezone: string;
  // Email Communications System doc's Preference Center - domain-scoped
  // opt-out (e.g. "BILL", "VOICE") on top of the single transactional
  // on/off switch above, matching NotificationTemplate.domain.
  disabled_domains: string[];
};

export function getNotificationPreferences(token: string): Promise<NotificationPreferences> {
  return request<NotificationPreferences>("/notifications/preferences", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function updateNotificationPreferences(
  token: string,
  updates: Partial<NotificationPreferences>
): Promise<NotificationPreferences> {
  return request<NotificationPreferences>("/notifications/preferences", {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(updates),
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
  documents: ComplianceDocument[];
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

// Who's actually logged in - login itself returns only a bare token, so
// this is the only way the console UI knows which role to show/hide
// sections for (see access-matrix cross-check below, in the console layout).
export type StaffRole = "support" | "compliance_officer" | "super_admin";

export type StaffProfile = {
  id: string;
  email: string;
  role: StaffRole;
  is_active: boolean;
};

export function getCurrentStaff(token: string): Promise<StaffProfile> {
  return request<StaffProfile>("/staff/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Staff team management (staff.manage_staff_accounts, SUPER_ADMIN only for
// create/deactivate/reactivate - the list itself is open to any staff role,
// same "GET is diagnostic" posture as everywhere else in this console).
export type StaffTeamMember = {
  id: string;
  email: string;
  role: StaffRole;
  is_active: boolean;
  created_at: string;
};

export function listStaffTeam(token: string): Promise<StaffTeamMember[]> {
  return request<StaffTeamMember[]>("/staff/team", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function createStaffTeamMember(
  token: string,
  input: { email: string; password: string; role: StaffRole }
): Promise<StaffTeamMember> {
  return request<StaffTeamMember>("/staff/team", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function deactivateStaffTeamMember(token: string, staffId: string): Promise<StaffTeamMember> {
  return request<StaffTeamMember>(`/staff/team/${staffId}/deactivate`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function reactivateStaffTeamMember(token: string, staffId: string): Promise<StaffTeamMember> {
  return request<StaffTeamMember>(`/staff/team/${staffId}/reactivate`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Event outbox backlog (ops.manage_event_outbox governs the flush action
// itself; this GET is open to any staff role, same diagnostic posture as
// kill-switches).
export type EventOutboxSummary = {
  pending_count: number;
  failing_count: number;
  oldest_pending_age_seconds: number | null;
};

export function getEventOutboxSummary(token: string): Promise<EventOutboxSummary> {
  return request<EventOutboxSummary>("/ops/event-outbox", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function flushEventOutbox(token: string): Promise<{ checked: number; published: number; failed: number }> {
  return request("/ops/event-outbox/flush", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Platform-wide call volume + subscription/revenue snapshot (Super Admin
// dashboard). estimated_mrr_minor_units is a planning estimate, not a
// real revenue figure - see the backend's own docstring
// (app.staff.service.get_platform_billing_metrics) for exactly what it
// does and doesn't account for.
export type PlatformCallMetrics = {
  window_days: number;
  total_calls: number;
  total_minutes: number;
  by_status: { status: string; count: number }[];
};

export type PlatformBillingMetrics = {
  total_active_subscriptions: number;
  estimated_mrr_minor_units: number;
  currency_code: string;
  by_plan: { plan_code: string; plan_name: string; count: number }[];
};

export type PlatformMetrics = {
  calls: PlatformCallMetrics;
  billing: PlatformBillingMetrics;
};

export function getPlatformMetrics(token: string): Promise<PlatformMetrics> {
  return request<PlatformMetrics>("/staff/platform-metrics", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type AccountOverview = {
  id: string;
  name: string;
  account_type: string;
  owner_email: string | null;
  member_count: number;
  number_count: number;
  // Already returned by the backend (AccountOverviewResponse) but never
  // surfaced in the UI until now - real depth worth showing a Super Admin
  // (billing risk/legal exposure per account), not meaningful to a role
  // with no capability to act on either.
  billing_classification: string;
  billing_source: string;
  is_test: boolean;
  legal_hold: boolean;
  legal_hold_reference: string | null;
  created_at: string;
};

export function listStaffAccounts(token: string): Promise<AccountOverview[]> {
  return request<AccountOverview[]>("/staff/accounts", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// is_test bypasses the CONTROLLED_BETA/INTERNAL_TEST market-activation gate
// for this account, at the cost of also blocking real Stripe/ZoikoNex
// billing while flagged - SUPER_ADMIN-only (accounts.manage_test_flag).
export function setAccountTestFlag(
  token: string, accountId: string, isTest: boolean, reason: string
): Promise<AccountOverview> {
  return request<AccountOverview>(`/staff/accounts/${accountId}/test-flag`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ is_test: isTest, reason }),
  });
}

// The role x capability grid that actually gates every sensitive staff
// action (Commercial Billing Operating Standard doc's "formal RBAC/
// segregation-of-duties matrix" ask) - see backend app.staff.models.
// StaffCapabilityGrant's docstring.

export type AccessMatrixEntry = {
  capability: string;
  roles: string[];
};

export function getAccessMatrix(staffToken: string): Promise<AccessMatrixEntry[]> {
  return request<AccessMatrixEntry[]>("/staff/access-matrix", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function grantCapability(staffToken: string, capability: string, role: string): Promise<void> {
  return request<void>(
    `/staff/access-matrix/${encodeURIComponent(capability)}/${encodeURIComponent(role)}`,
    { method: "PUT", headers: { Authorization: `Bearer ${staffToken}` } }
  );
}

export function revokeCapability(staffToken: string, capability: string, role: string): Promise<void> {
  return request<void>(
    `/staff/access-matrix/${encodeURIComponent(capability)}/${encodeURIComponent(role)}`,
    { method: "DELETE", headers: { Authorization: `Bearer ${staffToken}` } }
  );
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

export type DueRenewalEntry = {
  id: string;
  e164: string;
  country: string;
  status: string;
  account_id: string;
  account_name: string | null;
  account_owner_email: string | null;
  next_renewal_at: string | null;
};

export function listDueForRenewal(staffToken: string): Promise<DueRenewalEntry[]> {
  return request<DueRenewalEntry[]>("/staff/numbers/due-for-renewal", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function markNumberRenewed(
  staffToken: string,
  numberId: string
): Promise<{ id: string; e164: string; next_renewal_at: string | null }> {
  return request(`/staff/numbers/${numberId}/mark-renewed`, {
    method: "POST",
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
  // Only present for providers with a secondary-vendor failover path
  // configured (see integrations/_shared/circuit_breaker.py) - already
  // returned by the backend but never surfaced in the UI until now.
  circuit_state?: "closed" | "open" | "half_open";
  failover_enabled?: boolean;
};

export function listProviderStatuses(staffToken: string): Promise<{ providers: ProviderStatus[] }> {
  return request<{ providers: ProviderStatus[] }>("/ops/provider-status", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

// --- Distributed tracing (self-hosted, staff-only) ---

export type ProviderCallTrace = {
  id: string;
  request_id: string | null;
  provider: string;
  operation: string;
  duration_ms: number;
  success: boolean;
  error_detail: string | null;
  created_at: string;
};

export type ProviderLatencySummary = {
  provider: string;
  operation: string;
  count: number;
  avg_duration_ms: number;
  max_duration_ms: number;
  failure_count: number;
};

export function listProviderTraces(
  staffToken: string,
  filters: { provider?: string; requestId?: string; limit?: number } = {}
): Promise<ProviderCallTrace[]> {
  const params = new URLSearchParams();
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.requestId) params.set("request_id", filters.requestId);
  if (filters.limit) params.set("limit", String(filters.limit));
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<ProviderCallTrace[]>(`/ops/traces${query}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function getProviderTraceSummary(staffToken: string, hours: number = 24): Promise<ProviderLatencySummary[]> {
  return request<ProviderLatencySummary[]>(`/ops/traces/summary?hours=${hours}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

// --- Self-hosted error monitoring (staff-only) ---

export type ErrorEvent = {
  id: string;
  request_id: string;
  method: string;
  path: string;
  status_code: number;
  exception_type: string | null;
  exception_message: string | null;
  account_id: string | null;
  user_id: string | null;
  created_at: string;
};

export type ErrorEventDetail = ErrorEvent & {
  traceback: string | null;
};

export type ErrorCountSummary = {
  exception_type: string | null;
  path: string;
  status_code: number;
  count: number;
};

export function listErrors(staffToken: string, limit: number = 100): Promise<ErrorEvent[]> {
  return request<ErrorEvent[]>(`/ops/errors?limit=${limit}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function getErrorSummary(staffToken: string, hours: number = 24): Promise<ErrorCountSummary[]> {
  return request<ErrorCountSummary[]>(`/ops/errors/summary?hours=${hours}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function getErrorDetail(staffToken: string, errorId: string): Promise<ErrorEventDetail> {
  return request<ErrorEventDetail>(`/ops/errors/${errorId}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

// --- Incidents (public status page + staff management) ---

export type IncidentStatus = "investigating" | "monitoring" | "resolved";

export type Incident = {
  id: string;
  title: string;
  affected_service: string;
  status: IncidentStatus;
  impact_summary: string;
  mitigation_summary: string | null;
  started_at: string;
  resolved_at: string | null;
};

export function listIncidents(limit: number = 50): Promise<Incident[]> {
  return request<Incident[]>(`/ops/incidents?limit=${limit}`);
}

// --- Kill switches (staff-only, platform-wide or per-account) ---

export type KillSwitch = {
  id: string;
  scope: string;
  is_active: boolean;
  reason: string | null;
  activated_by: string | null;
  activated_at: string | null;
  deactivated_at: string | null;
  expires_at: string | null;
};

export function listKillSwitches(staffToken: string): Promise<KillSwitch[]> {
  return request<KillSwitch[]>("/ops/kill-switches", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function createIncident(
  staffToken: string,
  input: { title: string; affected_service: string; impact_summary: string }
): Promise<Incident> {
  return request<Incident>("/ops/incidents", {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
    body: JSON.stringify(input),
  });
}

export function updateIncident(
  staffToken: string,
  incidentId: string,
  input: { status: IncidentStatus; impact_summary?: string; mitigation_summary?: string }
): Promise<Incident> {
  return request<Incident>(`/ops/incidents/${incidentId}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${staffToken}` },
    body: JSON.stringify(input),
  });
}

export function resolveIncident(staffToken: string, incidentId: string): Promise<Incident> {
  return request<Incident>(`/ops/incidents/${incidentId}/resolve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

// --- Synthetic checks (staff-only) ---

export type SyntheticCheckRun = {
  id: string;
  check_name: string;
  success: boolean;
  duration_ms: number;
  detail: string | null;
  created_at: string;
};

export type SyntheticCheckSummary = {
  overall_healthy: boolean;
  checks: SyntheticCheckRun[];
};

export function runSyntheticChecks(staffToken: string): Promise<SyntheticCheckRun[]> {
  return request<SyntheticCheckRun[]>("/ops/synthetic-checks/run", {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function listSyntheticChecks(
  staffToken: string,
  filters: { checkName?: string; limit?: number } = {}
): Promise<SyntheticCheckRun[]> {
  const params = new URLSearchParams();
  if (filters.checkName) params.set("check_name", filters.checkName);
  if (filters.limit) params.set("limit", String(filters.limit));
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<SyntheticCheckRun[]>(`/ops/synthetic-checks${query}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function getSyntheticCheckSummary(staffToken: string): Promise<SyntheticCheckSummary> {
  return request<SyntheticCheckSummary>("/ops/synthetic-checks/summary", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

// --- ZoikoNex mock billing adapter (staff-only) ---
// No real ZoikoNex API exists yet - these routes simulate the webhook a
// real ZoikoNex would eventually send, so the graceful-degradation
// mechanism can be exercised. See backend app/integrations/billing/
// zoikonex.py's docstring.

export type ZoikoNexSyncEvent = {
  id: string;
  account_id: string;
  event_type: "subscription_sync" | "usage_sync" | "payment_event_received";
  zoikonex_ref: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type ZoikoNexReconciliationSummary = {
  total_subscriptions: number;
  synced_subscriptions: number;
  unsynced_subscriptions: number;
  total_usage_events: number;
  synced_usage_events: number;
  unsynced_usage_events: number;
};

export function simulateZoikoNexPaymentEvent(
  staffToken: string,
  accountId: string,
  eventType: "payment_failed" | "payment_retry" | "payment_restored"
): Promise<Subscription> {
  return request<Subscription>("/billing/zoikonex/simulate-payment-event", {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
    body: JSON.stringify({ account_id: accountId, event_type: eventType }),
  });
}

// --- Billing action requests (maker-checker: request now, approve/reject
// later, self-approval blocked server-side) ---

export type BillingActionRequest = {
  id: string;
  action_type: string;
  payload: Record<string, unknown>;
  requested_by: string;
  status: string;
  approved_by: string | null;
  rejection_reason: string | null;
  result: Record<string, unknown> | null;
  resolved_at: string | null;
  created_at: string;
};

export function listBillingActions(
  staffToken: string,
  requestStatus?: string
): Promise<BillingActionRequest[]> {
  const query = requestStatus ? `?request_status=${requestStatus}` : "";
  return request<BillingActionRequest[]>(`/billing/actions${query}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function listZoikoNexSyncLog(
  staffToken: string,
  filters: { accountId?: string; limit?: number } = {}
): Promise<ZoikoNexSyncEvent[]> {
  const params = new URLSearchParams();
  if (filters.accountId) params.set("account_id", filters.accountId);
  if (filters.limit) params.set("limit", String(filters.limit));
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<ZoikoNexSyncEvent[]>(`/billing/zoikonex/sync-log${query}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function getZoikoNexReconciliation(staffToken: string): Promise<ZoikoNexReconciliationSummary> {
  return request<ZoikoNexReconciliationSummary>("/billing/zoikonex/reconciliation", {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

// Architecture doc §9 "daily reconciliation jobs... exceptions must enter
// an operations queue" - unlike ZoikoNexReconciliationSummary above (a live
// aggregate), each run here is persisted history, and each specific
// out-of-sync record becomes an individually resolvable exception.

export type ZoikoNexReconciliationRun = {
  id: string;
  total_subscriptions: number;
  unsynced_subscriptions: number;
  total_usage_events: number;
  unsynced_usage_events: number;
  total_completed_calls: number;
  unmatched_completed_calls: number;
  exceptions_found: number;
  created_at: string;
};

export type ZoikoNexReconciliationException = {
  id: string;
  run_id: string;
  account_id: string;
  exception_type: "subscription_missing_zoikonex_ref" | "usage_event_missing_sync";
  subject_id: string;
  detail: string;
  resolved_at: string | null;
  resolved_by: string | null;
  resolution_reason: string | null;
  created_at: string;
};

export function runZoikoNexReconciliation(staffToken: string): Promise<ZoikoNexReconciliationRun> {
  return request<ZoikoNexReconciliationRun>("/billing/zoikonex/reconciliation/run", {
    method: "POST",
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function listZoikoNexReconciliationRuns(staffToken: string, limit = 50): Promise<ZoikoNexReconciliationRun[]> {
  return request<ZoikoNexReconciliationRun[]>(`/billing/zoikonex/reconciliation/runs?limit=${limit}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function listZoikoNexReconciliationExceptions(
  staffToken: string,
  filters: { resolved?: boolean; limit?: number } = {}
): Promise<ZoikoNexReconciliationException[]> {
  const params = new URLSearchParams();
  if (filters.resolved !== undefined) params.set("resolved", String(filters.resolved));
  if (filters.limit) params.set("limit", String(filters.limit));
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<ZoikoNexReconciliationException[]>(`/billing/zoikonex/reconciliation/exceptions${query}`, {
    headers: { Authorization: `Bearer ${staffToken}` },
  });
}

export function resolveZoikoNexReconciliationException(
  staffToken: string,
  exceptionId: string,
  reason: string
): Promise<ZoikoNexReconciliationException> {
  return request<ZoikoNexReconciliationException>(
    `/billing/zoikonex/reconciliation/exceptions/${exceptionId}/resolve`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${staffToken}` },
      body: JSON.stringify({ reason }),
    }
  );
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

// Mirrors backend SupportedCountryResponse - the real, live launch-market
// list. Confirmed live (2026-08-14) that the frontend previously used a
// hardcoded, stale country list (src/lib/sampleNumbers.ts) that offered 5
// countries the backend doesn't actually support (Nigeria, South Africa,
// Ghana, Kenya, Mexico - searching any of them fails with a real "not on
// Zoiko Local's supported country list yet" error) while hiding 4 the
// backend does support (Australia, Germany, France, India). Fetching this
// for real means the dropdown can never drift out of sync with the
// backend again.
export type SupportedCountry = {
  code: string;
  name: string;
  emergency_calling_supported: boolean;
  activation_state: string;
};

export function listSupportedCountries(token: string): Promise<SupportedCountry[]> {
  return request<SupportedCountry[]>("/numbers/countries", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type MyPhoneNumber = {
  id: string;
  e164: string;
  country: string;
  number_type: string;
  status: string;
  assigned_user_id: string | null;
  reserved_until: string | null;
  forwarding_number: string | null;
  business_hours_start: string | null;
  business_hours_end: string | null;
  business_hours_timezone: string;
  ai_receptionist_enabled: boolean;
  escalation_user_id: string | null;
  escalation_phone_number: string | null;
  call_flow_id: string | null;
  whatsapp_enabled: boolean;
  sms_enabled: boolean;
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
  address_requirements: string | null;
};

export type NumberRate = {
  country: string;
  number_type: string;
  recurring_price_cents: number;
  currency: string;
  is_placeholder: boolean;
};

export function listNumberRates(token: string): Promise<NumberRate[]> {
  return request<NumberRate[]>("/usage/number-rates", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type AIUsageRate = {
  overage_price_cents_per_minute: number;
  currency: string;
  is_placeholder: boolean;
};

export function getAIUsageRate(token: string): Promise<AIUsageRate | null> {
  return request<AIUsageRate | null>("/usage/ai-usage-rate", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// The $29/workspace/month AI Receptionist add-on itself is priced
// separately from the general AI overage rate above - its own versioned
// table (see backend AIReceptionistAddonRate's docstring).
export type AIReceptionistAddonRate = {
  catalog_version: string;
  monthly_price_minor_units: number;
  included_minutes: number;
  overage_rate_minor_units_per_minute: number;
  currency_code: string;
  is_placeholder: boolean;
};

export function getAIReceptionistAddonRate(token: string): Promise<AIReceptionistAddonRate | null> {
  return request<AIReceptionistAddonRate | null>("/billing/ai-receptionist-addon-rate", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

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
  input: { e164: string; country: string; number_type?: string }
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

// Real Stripe Checkout (test mode) - the customer-facing purchase button
// now goes through this instead of calling purchaseNumber() directly. The
// number only actually activates once Stripe confirms payment via the
// backend's webhook - see app.numbering.numbers.service.
// complete_number_purchase_from_checkout.
//
// included=true is the Global Plans, Pricing & Commercial Launch Standard
// doc's "first number is included with a paid plan" path - id/url are null
// (no Stripe session was created), and `number` is already ACTIVE (or
// COMPLIANCE_PENDING) since the backend purchased it immediately instead
// of waiting on a payment webhook.
export type CheckoutSession = {
  id: string | null;
  url: string | null;
  included: boolean;
  number: MyPhoneNumber | null;
  pending_charge_amount_minor_units: number | null;
};

export function createNumberCheckoutSession(token: string, e164: string): Promise<CheckoutSession> {
  return request<CheckoutSession>(`/numbers/${encodeURIComponent(e164)}/checkout-session`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type NumberEligibilityDocument = {
  document_type: string;
  storage_key: string;
  filename: string;
  content_type: string;
  uploaded_at: string;
};

export type NumberEligibilityCase = {
  id: string;
  phone_number_id: string;
  account_id: string;
  country: string;
  number_type: string;
  status: string;
  evidence: Record<string, unknown>[];
  review_notes: string | null;
  expires_at: string | null;
  created_at: string;
  resolved_at: string | null;
  documents: NumberEligibilityDocument[];
  twilio_bundle_sid: string | null;
  twilio_bundle_status: string | null;
  twilio_bundle_rejection_reason: string | null;
};

export function listMyEligibilityCases(token: string): Promise<NumberEligibilityCase[]> {
  return request<NumberEligibilityCase[]>("/numbers/eligibility-cases", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function submitEligibilityDocument(
  token: string,
  caseId: string,
  documentType: string,
  file: File
): Promise<NumberEligibilityCase> {
  const formData = new FormData();
  formData.append("document_type", documentType);
  formData.append("file", file);
  return request<NumberEligibilityCase>(`/numbers/eligibility-cases/${caseId}/documents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
}

export function submitEligibilityBundle(
  token: string,
  caseId: string,
  endUserAttributes: { first_name: string; last_name: string; email: string; phone_number: string },
  endUserType: string = "individual"
): Promise<NumberEligibilityCase> {
  return request<NumberEligibilityCase>(`/numbers/eligibility-cases/${caseId}/submit-bundle`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ end_user_attributes: endUserAttributes, end_user_type: endUserType }),
  });
}

export function syncEligibilityBundleStatus(token: string, caseId: string): Promise<NumberEligibilityCase> {
  return request<NumberEligibilityCase>(`/numbers/eligibility-cases/${caseId}/sync-bundle-status`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
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
    escalation_phone_number?: string | null;
    whatsapp_enabled?: boolean;
    sms_enabled?: boolean;
  }
): Promise<MyPhoneNumber> {
  return request<MyPhoneNumber>(`/numbers/${encodeURIComponent(e164)}/routing`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export type RingGroupDestination = {
  id: string;
  destination_number: string;
  ring_order: number;
};

export function getRingGroup(token: string, e164: string): Promise<RingGroupDestination[]> {
  return request<RingGroupDestination[]>(`/numbers/${encodeURIComponent(e164)}/ring-group`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function setRingGroup(token: string, e164: string, destinations: string[]): Promise<RingGroupDestination[]> {
  return request<RingGroupDestination[]>(`/numbers/${encodeURIComponent(e164)}/ring-group`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ destinations }),
  });
}

export type IVROption = {
  id: string;
  digit: string;
  destination_number: string;
};

export type IVRMenu = {
  greeting: string | null;
  options: IVROption[];
};

export function getIvrMenu(token: string, e164: string): Promise<IVRMenu> {
  return request<IVRMenu>(`/numbers/${encodeURIComponent(e164)}/ivr`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function setIvrMenu(
  token: string,
  e164: string,
  greeting: string,
  options: Record<string, string>
): Promise<IVRMenu> {
  return request<IVRMenu>(`/numbers/${encodeURIComponent(e164)}/ivr`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ greeting, options }),
  });
}

export function clearIvrMenu(token: string, e164: string): Promise<void> {
  return request<void>(`/numbers/${encodeURIComponent(e164)}/ivr`, {
    method: "DELETE",
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
  is_suspected_spam: boolean;
  created_at: string;
};

export function listCalls(token: string, limit?: number): Promise<CallLogEntry[]> {
  const query = limit ? `?limit=${limit}` : "";
  return request<CallLogEntry[]>(`/media/voice/calls${query}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// The stored recording_url is Twilio's own media URL, which needs Twilio's
// account credentials to fetch - opening it directly in a browser prompts
// for a login instead of playing audio. This calls our backend's proxy
// route instead (same Bearer-token pattern as exportAnalyticsCsv), which
// fetches the audio server-side and streams it back.
export async function getCallRecordingBlob(token: string, callSid: string): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/media/voice/calls/${callSid}/recording`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new ApiError("Couldn't load this recording.", response.status);
  }
  return response.blob();
}

export async function placeOutboundCall(
  token: string,
  input: { to: string; from: string; message?: string }
): Promise<{ sid: string; status: string; to: string; from: string }> {
  const fingerprint = await computeDeviceFingerprint();
  return request("/media/voice/outbound", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(fingerprint ? { "X-Device-Fingerprint": fingerprint } : {}),
    },
    body: JSON.stringify(input),
  });
}

export async function getBrowserVoiceToken(token: string): Promise<{ token: string }> {
  return request("/media/voice/browser-token", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function placeBridgeCall(
  token: string,
  input: { to: string; from: string; agent_number: string }
): Promise<{ sid: string; status: string; to: string; from: string }> {
  const fingerprint = await computeDeviceFingerprint();
  return request("/media/voice/bridge", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(fingerprint ? { "X-Device-Fingerprint": fingerprint } : {}),
    },
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

// See getCallRecordingBlob's comment - same reason a voicemail's raw
// recording_url can't be opened directly in a browser.
export async function getVoicemailRecordingBlob(token: string, voicemailId: string): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/media/voicemail/${voicemailId}/recording`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new ApiError("Couldn't load this recording.", response.status);
  }
  return response.blob();
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

export type ConsentRecordStatus = {
  consent_type: string;
  jurisdiction: string;
  granted_at: string | null;
  revoked_at: string | null;
};

export function listConsentStatus(token: string): Promise<ConsentRecordStatus[]> {
  return request<ConsentRecordStatus[]>("/compliance/consent", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- Video ---

export type VideoRoom = {
  room_name: string;
  status: "created" | "active" | "ended";
  started_at: string | null;
  ended_at: string | null;
  recording_in_progress: boolean;
  recording_failed: boolean;
  recording_url: string | null;
  participant_minutes: number;
  confidential: boolean;
  worst_connection_quality: "excellent" | "good" | "poor" | null;
  reconnect_count: number;
};

export function listVideoRooms(token: string): Promise<VideoRoom[]> {
  return request<VideoRoom[]>("/media/video/rooms", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function createVideoRoom(
  token: string,
  confidential: boolean = false
): Promise<{ room_name: string; status: string; confidential: boolean }> {
  return request("/media/video/rooms", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ confidential }),
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

// Deliberately no token param - a guest requests to join via the shared
// link + their name only, no Zoiko account (see backend's POST
// .../guest-token, which has no auth dependency at all). Doesn't return a
// token directly - the guest lands in the waiting room until the host
// admits them (see checkGuestWaitingStatus).
export function guestJoinVideoRoom(roomName: string, displayName: string): Promise<{ waiting_id: string }> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/guest-token`, {
    method: "POST",
    body: JSON.stringify({ display_name: displayName }),
  });
}

export type WaitingStatus = {
  status: "pending" | "admitted" | "denied" | "expired";
  token: string | null;
  url: string | null;
  recording: boolean;
};

export function checkGuestWaitingStatus(roomName: string, waitingId: string): Promise<WaitingStatus> {
  return request(
    `/media/video/rooms/${encodeURIComponent(roomName)}/waiting/${encodeURIComponent(waitingId)}`
  );
}

export type WaitingGuest = { id: string; display_name: string; created_at: string };

export function listWaitingGuests(token: string, roomName: string): Promise<WaitingGuest[]> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/waiting`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function admitWaitingGuest(token: string, roomName: string, waitingId: string): Promise<{ admitted: boolean }> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/waiting/${encodeURIComponent(waitingId)}/admit`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function denyWaitingGuest(token: string, roomName: string, waitingId: string): Promise<{ denied: boolean }> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/waiting/${encodeURIComponent(waitingId)}/deny`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
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

export function stopVideoRecording(
  token: string,
  roomName: string
): Promise<{ room_name: string; recording: boolean }> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/recording/stop`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function reportCallQuality(
  token: string,
  roomName: string,
  quality: "excellent" | "good" | "poor",
  reconnected: boolean = false
): Promise<{ recorded: boolean }> {
  return request(`/media/video/rooms/${encodeURIComponent(roomName)}/quality`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ quality, reconnected }),
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
  is_likely_spam: boolean;
  spam_reason: string | null;
  callback_preference: string | null;
  callback_requested: boolean;
  callback_window: "asap" | "today" | "tomorrow" | null;
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

// --- Billing / Plans / Entitlements ---
// No payment processing - this only gates feature/resource limits locally,
// pending a real ZoikoNex connection (see backend app/billing/models.py).

export type Plan = {
  plan_code: string;
  name: string;
  max_numbers: number;
  max_team_seats: number;
  monthly_voice_minutes: number;
  monthly_video_minutes: number;
  monthly_ai_summaries: number;
  included_ai_receptionist_minutes: number;
  trial_days: number;
};

export type BillingPeriod = "monthly" | "annual";

export type Subscription = {
  id: string;
  plan_code: string;
  status: "trialing" | "active" | "past_due" | "canceled";
  billing_period: BillingPeriod;
  ai_receptionist_addon_enabled: boolean;
  trial_ends_at: string | null;
  current_period_start: string;
  current_period_end: string;
  zoikonex_ref: string | null;
  grace_period_ends_at: string | null;
  canceled_at: string | null;
};

// Mirrors backend PriceCatalogEntryResponse. null means no price has ever
// been set for this plan (e.g. Enterprise, which is sales-led/custom per
// the Global Plans, Pricing & Commercial Launch Standard - never show a
// dollar amount for it, show "Custom" instead).
export type PriceCatalogEntry = {
  id: string;
  plan_code: string;
  catalog_version: string;
  billing_period: BillingPeriod;
  amount_minor_units: number;
  currency_code: string;
  status: string;
  is_placeholder: boolean;
  price_book_version: string | null;
  market: string;
  effective_from: string | null;
  effective_to: string | null;
  approval_evidence: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
};

export type UsageResourceSummary = {
  resource: string;
  used: number;
  limit: number;
};

export type UsageSummary = {
  plan_code: string;
  plan_name: string;
  status: string;
  trial_ends_at: string | null;
  current_period_start: string;
  current_period_end: string;
  resources: UsageResourceSummary[];
};

export function listPlans(token: string): Promise<Plan[]> {
  return request<Plan[]>("/billing/plans", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function getSubscription(token: string): Promise<Subscription> {
  return request<Subscription>("/billing/subscription", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function changeSubscriptionPlan(
  token: string,
  planCode: string,
  billingPeriod: BillingPeriod = "monthly"
): Promise<Subscription> {
  return request<Subscription>("/billing/subscription/plan", {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ plan_code: planCode, billing_period: billingPeriod }),
  });
}

export interface PlanChangeCheckoutSession {
  id: string;
  url: string;
}

export function createPlanChangeCheckoutSession(
  token: string,
  planCode: string,
  billingPeriod: BillingPeriod = "monthly"
): Promise<PlanChangeCheckoutSession> {
  return request<PlanChangeCheckoutSession>("/billing/subscription/plan/checkout-session", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ plan_code: planCode, billing_period: billingPeriod }),
  });
}

export function setAIReceptionistAddon(token: string, enabled: boolean): Promise<Subscription> {
  return request<Subscription>("/billing/subscription/ai-receptionist-addon", {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ enabled }),
  });
}

export function cancelSubscription(token: string, reason?: string): Promise<Subscription> {
  return request<Subscription>("/billing/subscription/cancel", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

// null means this plan has no price on file yet (only expected for
// Enterprise, which is sales-led/custom).
export function getPriceCatalogEntry(
  token: string,
  planCode: string,
  billingPeriod: BillingPeriod = "monthly"
): Promise<PriceCatalogEntry | null> {
  return request<PriceCatalogEntry | null>(
    `/billing/price-catalog/${planCode}?billing_period=${billingPeriod}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
}

export type PublicSupportedCountry = {
  code: string;
  name: string;
};

// Unauthenticated mirrors of listPlans/getPriceCatalogEntry above, for the
// public /pricing marketing page - no visitor account exists yet to send a
// token with. Same response shapes, real catalog-driven data (never
// hardcoded), just without an Authorization header.
export function listPublicPlans(): Promise<Plan[]> {
  return request<Plan[]>("/billing/public/plans");
}

export function getPublicPlanPrice(
  planCode: string,
  billingPeriod: BillingPeriod = "monthly"
): Promise<PriceCatalogEntry | null> {
  return request<PriceCatalogEntry | null>(
    `/billing/public/plans/${planCode}/price?billing_period=${billingPeriod}`
  );
}

// Filtered server-side to markets that are actually PAID_OPEN today - see
// backend app.billing.routes.list_public_countries.
export function listPublicCountries(): Promise<PublicSupportedCountry[]> {
  return request<PublicSupportedCountry[]>("/billing/public/countries");
}

export function getUsageSummary(token: string): Promise<UsageSummary> {
  return request<UsageSummary>("/billing/usage-summary", {
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

// Fraud case review queue and scoring rules are defined further down,
// alongside listFraudRules/upsertFraudRule (see "Fraud model" section).

// --- Contacts ---

export type Contact = {
  id: string;
  account_id: string;
  name: string;
  phone_number: string;
  email: string | null;
  notes: string | null;
  created_by_user_id: string | null;
  created_at: string;
};

export type ContactInput = {
  name: string;
  phone_number: string;
  email?: string | null;
  notes?: string | null;
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

export function createContact(token: string, input: ContactInput): Promise<Contact> {
  return request<Contact>("/contacts", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function updateContact(token: string, contactId: string, input: ContactInput): Promise<Contact> {
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

// --- Call Flows (Advanced IVR builder, Phase 3) ---

export type CallFlowNodeType =
  | "menu"
  | "business_hours"
  | "forward"
  | "queue"
  | "voicemail"
  | "ai_receptionist"
  | "hangup";

export type CallFlowNode = {
  id: string;
  type: CallFlowNodeType;
  prompt?: string | null;
  options?: Record<string, string> | null;
  invalid_node_id?: string | null;
  timeout_node_id?: string | null;
  start?: string | null;
  end?: string | null;
  timezone?: string | null;
  within_node_id?: string | null;
  outside_node_id?: string | null;
  destinations?: string[] | null;
  on_no_answer_node_id?: string | null;
  queue_id?: string | null;
  overflow_node_id?: string | null;
  message?: string | null;
};

export type CallFlowVersion = {
  id: string;
  version: number;
  status: "draft" | "published" | "archived";
  entry_node_id: string;
  nodes: CallFlowNode[];
  published_at: string | null;
  rolled_back_from_version: number | null;
  created_at: string;
};

export type CallFlowSummary = {
  id: string;
  name: string;
  created_at: string;
  has_draft: boolean;
  live_version: number | null;
  assigned_numbers: string[];
};

export type CallFlowDetail = {
  id: string;
  account_id: string;
  name: string;
  created_at: string;
  draft: CallFlowVersion | null;
  live: CallFlowVersion | null;
  version_history: CallFlowVersion[];
};

export type PublishResult = {
  published: boolean;
  errors: string[];
  version: CallFlowVersion | null;
};

export function listCallFlows(token: string): Promise<CallFlowSummary[]> {
  return request<CallFlowSummary[]>("/call-flows", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- Developer Webhooks ---

export type WebhookEndpoint = {
  id: string;
  url: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
};

export type WebhookEndpointCreated = WebhookEndpoint & { secret: string };

export type WebhookDelivery = {
  id: string;
  endpoint_id: string;
  event_type: string;
  status: "delivered" | "failed";
  response_status_code: number | null;
  error: string | null;
  created_at: string;
};

export function listWebhookEndpoints(token: string): Promise<WebhookEndpoint[]> {
  return request<WebhookEndpoint[]>("/webhooks/endpoints", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function createCallFlow(token: string, name: string): Promise<CallFlowSummary> {
  return request<CallFlowSummary>("/call-flows", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ name }),
  });
}

export function getCallFlow(token: string, callFlowId: string): Promise<CallFlowDetail> {
  return request<CallFlowDetail>(`/call-flows/${encodeURIComponent(callFlowId)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function saveCallFlowDraft(
  token: string,
  callFlowId: string,
  input: { entry_node_id: string; nodes: CallFlowNode[] }
): Promise<CallFlowVersion> {
  return request<CallFlowVersion>(`/call-flows/${encodeURIComponent(callFlowId)}/draft`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function validateCallFlowDraft(token: string, callFlowId: string): Promise<PublishResult> {
  return request<PublishResult>(`/call-flows/${encodeURIComponent(callFlowId)}/validate`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function publishCallFlow(token: string, callFlowId: string): Promise<PublishResult> {
  return request<PublishResult>(`/call-flows/${encodeURIComponent(callFlowId)}/publish`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function rollbackCallFlow(token: string, callFlowId: string, version: number): Promise<CallFlowVersion> {
  return request<CallFlowVersion>(`/call-flows/${encodeURIComponent(callFlowId)}/rollback`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ version }),
  });
}

export function assignCallFlow(
  token: string,
  callFlowId: string,
  phoneNumberId: string
): Promise<{ phone_number_id: string; call_flow_id: string | null }> {
  return request(`/call-flows/${encodeURIComponent(callFlowId)}/assign`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ phone_number_id: phoneNumberId }),
  });
}

export function unassignCallFlow(
  token: string,
  phoneNumberId: string
): Promise<{ phone_number_id: string; call_flow_id: string | null }> {
  return request(`/call-flows/unassign/${encodeURIComponent(phoneNumberId)}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- Call Queues (contact-center-lite, Phase 3) ---

export type QueueMember = { user_id: string; email: string };

export type CallQueue = {
  id: string;
  account_id: string;
  name: string;
  max_wait_seconds: number;
  wrap_up_seconds: number;
  created_at: string;
  members: QueueMember[];
};

export type QueueStatus = {
  queue_id: string;
  waiting_count: number;
  in_progress_count: number;
  longest_wait_seconds: number;
  sla_breached: boolean;
};

export type AgentPresence = {
  status: "available" | "wrap_up" | "offline";
  changed_at: string;
  wrap_up_until: string | null;
  effectively_available: boolean;
};

export type PullNextResult = {
  call_sid: string;
  caller_number: string;
  queue_call_log_id: string;
};

export function listQueues(token: string): Promise<CallQueue[]> {
  return request<CallQueue[]>("/queues", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function createQueue(
  token: string,
  input: { name: string; max_wait_seconds?: number; wrap_up_seconds?: number }
): Promise<CallQueue> {
  return request<CallQueue>("/queues", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function createWebhookEndpoint(
  token: string,
  input: { url: string; description?: string }
): Promise<WebhookEndpointCreated> {
  return request<WebhookEndpointCreated>("/webhooks/endpoints", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function updateQueue(
  token: string,
  queueId: string,
  input: { name?: string; max_wait_seconds?: number; wrap_up_seconds?: number }
): Promise<CallQueue> {
  return request<CallQueue>(`/queues/${encodeURIComponent(queueId)}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function addQueueMember(token: string, queueId: string, userId: string): Promise<CallQueue> {
  return request<CallQueue>(`/queues/${encodeURIComponent(queueId)}/members`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ user_id: userId }),
  });
}

export function removeQueueMember(token: string, queueId: string, userId: string): Promise<CallQueue> {
  return request<CallQueue>(`/queues/${encodeURIComponent(queueId)}/members/${encodeURIComponent(userId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function deleteWebhookEndpoint(token: string, endpointId: string): Promise<void> {
  return request<void>(`/webhooks/endpoints/${encodeURIComponent(endpointId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function getQueueStatus(token: string, queueId: string): Promise<QueueStatus> {
  return request<QueueStatus>(`/queues/${encodeURIComponent(queueId)}/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function listWebhookDeliveries(token: string, endpointId?: string): Promise<WebhookDelivery[]> {
  const qs = endpointId ? `?endpoint_id=${encodeURIComponent(endpointId)}` : "";
  return request<WebhookDelivery[]>(`/webhooks/deliveries${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function pullNextCaller(token: string, queueId: string): Promise<PullNextResult> {
  return request<PullNextResult>(`/queues/${encodeURIComponent(queueId)}/pull-next`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function getMyPresence(token: string): Promise<AgentPresence> {
  return request<AgentPresence>("/queues/presence/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function setMyPresence(token: string, status: "available" | "offline"): Promise<AgentPresence> {
  return request<AgentPresence>("/queues/presence/me", {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ status }),
  });
}

// --- Business messaging: WhatsApp + SMS (Phase 3) ---

export type MessagingChannel = "whatsapp" | "sms";

export type MessagingConversation = {
  id: string;
  phone_number_id: string;
  customer_number: string;
  channel: MessagingChannel;
  opted_out: boolean;
  last_message_at: string;
  created_at: string;
};

export type MessagingMessage = {
  id: string;
  direction: "inbound" | "outbound";
  body: string;
  status: string;
  created_at: string;
};

export function listConversations(token: string): Promise<MessagingConversation[]> {
  return request<MessagingConversation[]>("/messaging/conversations", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- Public API keys ---

export type ApiKey = {
  id: string;
  label: string;
  key_prefix: string;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

export type ApiKeyCreated = ApiKey & { raw_key: string };

export function listApiKeys(token: string): Promise<ApiKey[]> {
  return request<ApiKey[]>("/developer/api-keys", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function createApiKey(token: string, label: string): Promise<ApiKeyCreated> {
  return request<ApiKeyCreated>("/developer/api-keys", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ label }),
  });
}

export function revokeApiKey(token: string, keyId: string): Promise<void> {
  return request<void>(`/developer/api-keys/${encodeURIComponent(keyId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

// --- CRM connection (HubSpot: real OAuth - see backend/app/integrations/crm/hubspot.py;
// Salesforce/Pipedrive: still mock - see backend/app/integrations/crm/mock.py) ---

export type CrmProvider = "hubspot" | "salesforce" | "pipedrive";

export type CrmConnection = {
  id: string;
  provider: CrmProvider;
  external_account_label: string;
  connected_at: string;
};

export type CrmSyncEvent = {
  id: string;
  event_type: "contact_sync" | "activity_sync";
  external_ref: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export function getCrmConnection(token: string): Promise<CrmConnection | null> {
  return request<CrmConnection | null>("/crm/connection", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function listConversationMessages(token: string, conversationId: string): Promise<MessagingMessage[]> {
  return request<MessagingMessage[]>(`/messaging/conversations/${encodeURIComponent(conversationId)}/messages`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function sendWhatsAppMessage(
  token: string,
  input: { phone_number_id: string; to: string; body: string }
): Promise<MessagingMessage> {
  return request<MessagingMessage>("/messaging/whatsapp/send", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function sendSms(
  token: string,
  input: { phone_number_id: string; to: string; body: string }
): Promise<MessagingMessage> {
  return request<MessagingMessage>("/messaging/sms/send", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export type AnalyticsDailyPoint = {
  date: string;
  calls: number;
  call_minutes: number;
  video_minutes: number;
  messages: number;
};

export type AnalyticsOverview = {
  range_days: number;
  total_calls: number;
  total_call_minutes: number;
  total_video_minutes: number;
  total_messages: number;
  active_numbers: number;
  ai_summaries: number;
  daily: AnalyticsDailyPoint[];
};

export function getAnalyticsOverview(token: string, days: number): Promise<AnalyticsOverview> {
  return request<AnalyticsOverview>(`/analytics/overview?days=${days}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function exportAnalyticsCsv(token: string, days: number): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/analytics/export.csv?days=${days}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new ApiError("Couldn't export the report.", response.status);
  }
  return response.blob();
}

export function connectCrm(token: string, provider: CrmProvider): Promise<CrmConnection> {
  return request<CrmConnection>("/crm/connect", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ provider }),
  });
}

// HubSpot has a real OAuth flow - /crm/connect rejects provider="hubspot".
// This returns HubSpot's own consent-screen URL; the caller redirects the
// browser there (window.location.href = authorize_url), HubSpot redirects
// back to /crm/hubspot/callback on the API itself (not this frontend),
// which then redirects the browser to this page with ?crm=connected|error.
export function getHubspotAuthorizeUrl(token: string): Promise<{ authorize_url: string }> {
  return request<{ authorize_url: string }>("/crm/hubspot/authorize", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Same real-OAuth shape as HubSpot above, for Salesforce.
export function getSalesforceAuthorizeUrl(token: string): Promise<{ authorize_url: string }> {
  return request<{ authorize_url: string }>("/crm/salesforce/authorize", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Same real-OAuth shape as HubSpot above, for Pipedrive - all three
// CrmProvider values are real now; /crm/connect rejects all of them.
export function getPipedriveAuthorizeUrl(token: string): Promise<{ authorize_url: string }> {
  return request<{ authorize_url: string }>("/crm/pipedrive/authorize", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function disconnectCrm(token: string): Promise<void> {
  return request<void>("/crm/disconnect", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function listCrmSyncEvents(token: string): Promise<CrmSyncEvent[]> {
  return request<CrmSyncEvent[]>("/crm/sync-log", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Fraud model (Architecture doc Phase 4 "proprietary fraud models") - staff
// review queue for accounts whose decayed risk score crossed REVIEW_THRESHOLD,
// and staff-tunable per-signal-type scoring weights (same "rules as data"
// doctrine as ComplianceRule/BlockedDestination).
export type RiskSignalType =
  | "velocity_exceeded"
  | "blocked_destination_attempt"
  | "geographic_dispersion";

export type FraudCaseStatus = "open" | "confirmed" | "cleared";

export type FraudRule = {
  id: string;
  signal_type: RiskSignalType;
  weight: number;
  is_active: boolean;
  created_at: string;
};

export type FraudCase = {
  id: string;
  account_id: string;
  score_at_open: number;
  status: FraudCaseStatus;
  resolved_by: string | null;
  resolution_notes: string | null;
  created_at: string;
  resolved_at: string | null;
};

export function listFraudRules(token: string): Promise<FraudRule[]> {
  return request<FraudRule[]>("/risk/fraud-rules", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function upsertFraudRule(
  token: string,
  signalType: RiskSignalType,
  input: { weight: number; is_active: boolean }
): Promise<FraudRule> {
  return request<FraudRule>(`/risk/fraud-rules/${signalType}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
}

export function listFraudCases(
  token: string,
  status?: FraudCaseStatus
): Promise<FraudCase[]> {
  const query = status ? `?case_status=${status}` : "";
  return request<FraudCase[]>(`/risk/fraud-cases${query}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function resolveFraudCase(
  token: string,
  caseId: string,
  status: Exclude<FraudCaseStatus, "open">,
  notes?: string
): Promise<FraudCase> {
  return request<FraudCase>(`/risk/fraud-cases/${caseId}/resolve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ status, notes }),
  });
}
