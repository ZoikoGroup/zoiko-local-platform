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
