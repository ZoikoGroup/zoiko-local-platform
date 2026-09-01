"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getCurrentStaff, getAccessMatrix, type StaffRole } from "@/lib/api";
import { useStaffToken } from "@/lib/staffAuth";

interface StaffRoleContextValue {
  // null while still loading (or logged out) - callers should treat that
  // the same as "show nothing gated yet" rather than "show everything",
  // so a SUPPORT/COMPLIANCE_OFFICER account never gets a flash of
  // SUPER_ADMIN-only content before the real role loads.
  role: StaffRole | null;
  // Every capability key this role is actually granted, straight from
  // GET /staff/access-matrix (the same table StaffCapabilityGrant backs) -
  // deliberately not a second, hand-maintained copy of "what SUPPORT can
  // do" living in frontend code, which could silently drift from the real
  // grants an admin edits on the Access Matrix page.
  capabilities: Set<string>;
  loading: boolean;
}

const StaffRoleContext = createContext<StaffRoleContextValue>({
  role: null,
  capabilities: new Set(),
  loading: true,
});

export function StaffRoleProvider({ children }: { children: ReactNode }) {
  const { token } = useStaffToken();
  const [role, setRole] = useState<StaffRole | null>(null);
  const [capabilities, setCapabilities] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // No setState here for the logged-out case - this whole provider tree
    // unmounts the moment a page redirects to /staff/login (a different
    // route, outside this layout), so there's nothing to reset back to.
    // role/capabilities/loading's initial useState values already are the
    // correct "not logged in yet" state.
    if (!token) return;
    Promise.all([getCurrentStaff(token), getAccessMatrix(token)])
      .then(([staff, matrix]) => {
        setRole(staff.role);
        setCapabilities(new Set(matrix.filter((entry) => entry.roles.includes(staff.role)).map((entry) => entry.capability)));
      })
      .catch(() => {
        // Left as null/empty - every gated section just stays hidden
        // rather than the page crashing; the pages themselves already
        // handle a dead token by redirecting to /staff/login.
      })
      .finally(() => setLoading(false));
  }, [token]);

  return (
    <StaffRoleContext.Provider value={{ role, capabilities, loading }}>{children}</StaffRoleContext.Provider>
  );
}

export function useStaffRole() {
  return useContext(StaffRoleContext);
}

// True if this role can perform at least one of the given capabilities -
// or if the caller passed no capabilities at all (an ungated, any-staff
// page like Overview/Accounts/Provider Status/Audit Log).
export function hasAnyCapability(granted: Set<string>, required?: string[]): boolean {
  if (!required || required.length === 0) return true;
  return required.some((c) => granted.has(c));
}
