const STAFF_TOKEN_KEY = "zoiko_staff_token";

export function saveStaffToken(token: string) {
  localStorage.setItem(STAFF_TOKEN_KEY, token);
}

export function getStaffToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(STAFF_TOKEN_KEY);
}

export function clearStaffToken() {
  localStorage.removeItem(STAFF_TOKEN_KEY);
}
