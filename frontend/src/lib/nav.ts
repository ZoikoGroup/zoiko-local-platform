// requiredEntitlement, when present, names a key from the backend's
// GET /billing/entitlements snapshot (ZL-COM-ENT-001 v3.0) - the nav item
// locks (in addition to the existing trial-only lock) when the caller's
// current plan doesn't grant it. Left unset for items with no natural
// single-key mapping (Home, Calls, Video, Voicemail, Contacts, Billing,
// Notifications, Security, Compliance, Settings, Support) - those stay
// gated by trial status alone, same as before this feature.
export const NAV_ITEMS = [
  { label: "Home", href: "/dashboard" },
  { label: "My Numbers", href: "/dashboard/numbers" },
  { label: "Call Flows", href: "/dashboard/call-flows", requiredEntitlement: "routing.advanced" },
  { label: "Queues", href: "/dashboard/queues", requiredEntitlement: "routing.shared" },
  { label: "Messaging", href: "/dashboard/messaging" },
  { label: "Calls", href: "/dashboard/calls" },
  { label: "Video", href: "/dashboard/video" },
  { label: "AI Center", href: "/dashboard/ai-insights" },
  { label: "Voicemail", href: "/dashboard/voicemail" },
  { label: "Contacts", href: "/dashboard/contacts" },
  { label: "Analytics", href: "/dashboard/reports", requiredEntitlement: "reporting.advanced" },
  { label: "Billing", href: "/dashboard/billing" },
  { label: "Notifications", href: "/dashboard/notifications" },
  { label: "Business", href: "/dashboard/business", requiredEntitlement: "team.enabled" },
  { label: "Security", href: "/dashboard/security" },
  { label: "Compliance", href: "/dashboard/compliance" },
  { label: "Settings", href: "/dashboard/settings" },
  { label: "Support", href: "/dashboard/support" },
];

export function currentPageLabel(pathname: string | null): string {
  if (!pathname) return "Dashboard";
  const match = NAV_ITEMS.find((item) =>
    item.href === "/dashboard" ? pathname === "/dashboard" : pathname.startsWith(item.href)
  );
  return match?.label ?? "Dashboard";
}
