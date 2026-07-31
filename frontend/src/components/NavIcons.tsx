type IconProps = { className?: string };

const base = "w-[18px] h-[18px]";

export function DashboardIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  );
}

export function PhoneIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M4 5c0-1 1-2 2-2h2l2 5-2 1.5a10 10 0 0 0 5 5L14.5 13l5 2v2c0 1-1 2-2 2C10 19 4 13 4 5Z"
      />
    </svg>
  );
}

export function CallLogIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M4 5c0-1 1-2 2-2h2l2 5-2 1.5a10 10 0 0 0 5 5L14.5 13l5 2v2c0 1-1 2-2 2C10 19 4 13 4 5Z"
      />
      <path strokeLinecap="round" d="M15 3l3 3-3 3M18 6h-5" />
    </svg>
  );
}

export function VideoIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="6" width="12" height="12" rx="2" />
      <path strokeLinecap="round" strokeLinejoin="round" d="m15 10 6-3v10l-6-3" />
    </svg>
  );
}

export function SparkleIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"
      />
    </svg>
  );
}

export function ContactsIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="8" r="3.2" />
      <path strokeLinecap="round" d="M5 20c0-3.5 3-6 7-6s7 2.5 7 6" />
    </svg>
  );
}

export function BillingIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path strokeLinecap="round" d="M3 9.5h18" />
    </svg>
  );
}

export function IntegrationsIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M9 3h2v2.5a1.5 1.5 0 1 0 2 0V3h2a2 2 0 0 1 2 2v2h2.5a1.5 1.5 0 1 1 0 2H17v2a2 2 0 0 1-2 2h-2v2.5a1.5 1.5 0 1 1-2 0V13H9a2 2 0 0 1-2-2v-2H4.5a1.5 1.5 0 1 1 0-2H7V5a2 2 0 0 1 2-2Z"
      />
    </svg>
  );
}

export function ReportsIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path strokeLinecap="round" strokeLinejoin="round" d="M4 20V10M11 20V4M18 20v-7" />
    </svg>
  );
}

export function SettingsIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="3" />
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19.4 13a7.5 7.5 0 0 0 0-2l2-1.6-2-3.4-2.4.6a7.6 7.6 0 0 0-1.7-1L15 3h-4l-.3 2.6a7.6 7.6 0 0 0-1.7 1l-2.4-.6-2 3.4L6.6 11a7.5 7.5 0 0 0 0 2l-2 1.6 2 3.4 2.4-.6a7.6 7.6 0 0 0 1.7 1L11 21h4l.3-2.6a7.6 7.6 0 0 0 1.7-1l2.4.6 2-3.4-2-1.6Z"
      />
    </svg>
  );
}

export const NAV_ICONS: Record<string, (props: IconProps) => React.JSX.Element> = {
  "/dashboard": DashboardIcon,
  "/dashboard/numbers": PhoneIcon,
  "/dashboard/calls": CallLogIcon,
  "/dashboard/video": VideoIcon,
  "/dashboard/ai-insights": SparkleIcon,
  "/dashboard/contacts": ContactsIcon,
  "/dashboard/billing": BillingIcon,
  "/dashboard/integrations": IntegrationsIcon,
  "/dashboard/reports": ReportsIcon,
  "/dashboard/settings": SettingsIcon,
};
