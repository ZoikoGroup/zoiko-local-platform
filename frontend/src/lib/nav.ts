export const NAV_ITEMS = [
  { label: "Home", href: "/dashboard" },
  { label: "My Numbers", href: "/dashboard/numbers" },
  { label: "Calls", href: "/dashboard/calls" },
  { label: "Video", href: "/dashboard/video" },
  { label: "AI Center", href: "/dashboard/ai-insights" },
  { label: "Voicemail", href: "/dashboard/voicemail" },
  { label: "Contacts", href: "/dashboard/contacts" },
  { label: "Analytics", href: "/dashboard/reports" },
  { label: "Billing", href: "/dashboard/billing" },
  { label: "Notifications", href: "/dashboard/notifications" },
  { label: "Business", href: "/dashboard/business" },
  { label: "Security", href: "/dashboard/security" },
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
