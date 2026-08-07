export const NAV_ITEMS = [
  { label: "Dashboard", href: "/dashboard" },
  { label: "Phone Numbers", href: "/dashboard/numbers" },
  { label: "Call Flows", href: "/dashboard/call-flows" },
  { label: "Queues", href: "/dashboard/queues" },
  { label: "Messaging", href: "/dashboard/messaging" },
  { label: "Calls", href: "/dashboard/calls" },
  { label: "Video", href: "/dashboard/video" },
  { label: "AI Insights", href: "/dashboard/ai-insights" },
  { label: "Contacts", href: "/dashboard/contacts" },
  { label: "Billing & Usage", href: "/dashboard/billing" },
  { label: "Integrations", href: "/dashboard/integrations" },
  { label: "Reports", href: "/dashboard/reports" },
  { label: "Settings", href: "/dashboard/settings" },
];

export function currentPageLabel(pathname: string | null): string {
  if (!pathname) return "Dashboard";
  const match = NAV_ITEMS.find((item) =>
    item.href === "/dashboard" ? pathname === "/dashboard" : pathname.startsWith(item.href)
  );
  return match?.label ?? "Dashboard";
}
