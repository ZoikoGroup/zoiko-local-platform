import { useSyncExternalStore } from "react";

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

function subscribeToStorage(callback: () => void) {
  window.addEventListener("storage", callback);
  return () => window.removeEventListener("storage", callback);
}

/**
 * Reads the staff token only after hydration, not during the initial render -
 * `getStaffToken()` returns null on the server (no localStorage) but a real
 * value on the client, so returning it directly during the server render
 * would make the server-rendered HTML and the client's first hydration pass
 * disagree, which React flags as a hydration mismatch. useSyncExternalStore
 * is the sanctioned way to do this without an effect+setState round-trip
 * (which react-hooks/set-state-in-effect now flags as an anti-pattern): its
 * getServerSnapshot always returns the SSR-safe value, and it re-renders
 * automatically once the client's real snapshot differs post-hydration - as
 * a bonus, it also picks up storage events, so staff logout in one tab now
 * reflects in others without a manual refresh.
 *
 * `ready` distinguishes "haven't checked localStorage yet" from "checked,
 * and there really is no token" - callers' own redirect-to-login effects
 * must wait for `ready` before treating a null token as "log out," or
 * they'll fire on token's initial null and bounce an actually-logged-in
 * user back to the login page before hydration completes.
 */
export function useStaffToken(): { token: string | null; ready: boolean } {
  const token = useSyncExternalStore(subscribeToStorage, getStaffToken, () => null);
  const ready = useSyncExternalStore(
    subscribeToStorage,
    () => true,
    () => false
  );
  return { token, ready };
}
