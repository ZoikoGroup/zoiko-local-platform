import { useEffect, useState } from "react";

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

/**
 * Reads the staff token only after mount, not during the initial render -
 * `getStaffToken()` returns null on the server (no localStorage) but a real
 * value on the client, so calling it directly in a useState initializer
 * makes the server-rendered HTML and the client's first hydration pass
 * disagree, which React flags as a hydration mismatch. Starting at null on
 * both, then filling in the real value in an effect, keeps the first
 * render identical everywhere and only diverges after hydration completes.
 *
 * `ready` distinguishes "haven't checked localStorage yet" from "checked,
 * and there really is no token" - callers' own redirect-to-login effects
 * must wait for `ready` before treating a null token as "log out," or
 * they'll fire on token's initial null and bounce an actually-logged-in
 * user back to the login page before this hook's own effect runs.
 */
export function useStaffToken(): { token: string | null; ready: boolean } {
  const [state, setState] = useState<{ token: string | null; ready: boolean }>({ token: null, ready: false });
  useEffect(() => {
    setState({ token: getStaffToken(), ready: true });
  }, []);
  return state;
}
