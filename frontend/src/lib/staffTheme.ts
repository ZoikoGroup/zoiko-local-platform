"use client";

import { useEffect, useState } from "react";

const STAFF_THEME_KEY = "zoiko_staff_theme";
export type StaffTheme = "dark" | "light";

// Defaults to "dark" (this console's original, only theme) for both the
// server render and the first client render, then syncs from localStorage
// right after mount - avoids a server/client hydration mismatch, at the
// cost of a brief flash of dark mode for a returning light-mode user. Same
// tradeoff useStaffToken (staffAuth.ts) already accepts for the same
// reason: localStorage doesn't exist during SSR.
export function useStaffTheme(): [StaffTheme, (next: StaffTheme) => void] {
  const [theme, setTheme] = useState<StaffTheme>("dark");

  useEffect(() => {
    // Deferred a microtask out, not called directly in the effect body -
    // matches the pattern already established elsewhere in this console
    // (see staffRole.tsx) for reading state that then needs a setState.
    Promise.resolve().then(() => {
      const stored = window.localStorage.getItem(STAFF_THEME_KEY);
      if (stored === "light" || stored === "dark") setTheme(stored);
    });
  }, []);

  function setAndPersistTheme(next: StaffTheme) {
    setTheme(next);
    window.localStorage.setItem(STAFF_THEME_KEY, next);
  }

  return [theme, setAndPersistTheme];
}
