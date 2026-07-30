const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type User = {
  id: string;
  email: string;
  role: string;
  account_id: string;
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

export function login(input: {
  email: string;
  password: string;
}): Promise<{ access_token: string; token_type: string }> {
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
