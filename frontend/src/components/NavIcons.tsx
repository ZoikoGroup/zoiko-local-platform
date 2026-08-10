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

export function CallFlowIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="5" cy="6" r="2" />
      <circle cx="19" cy="6" r="2" />
      <circle cx="12" cy="18" r="2" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 8v3a3 3 0 0 0 3 3h1M19 8v3a3 3 0 0 1-3 3h-1M12 14v2" />
    </svg>
  );
}

export function QueueIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="8" cy="8" r="3" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 20c0-3 2-5 5-5s5 2 5 5" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M16 4a3 3 0 0 1 0 8M18 20c0-2.5-1.5-4.5-4-5" />
    </svg>
  );
}

export function MessagingIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 20l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
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

export function VoicemailIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="6" cy="15" r="3" />
      <circle cx="18" cy="15" r="3" />
      <path strokeLinecap="round" d="M6 12h12" />
    </svg>
  );
}

export function BellIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M6 10a6 6 0 1 1 12 0c0 4 1.5 5.5 1.5 5.5H4.5S6 14 6 10Z"
      />
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 19a2 2 0 0 0 4 0" />
    </svg>
  );
}

export function BriefcaseIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="7" width="18" height="12" rx="2" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path strokeLinecap="round" d="M3 12h18" />
    </svg>
  );
}

export function ShieldIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3Z"
      />
      <path strokeLinecap="round" strokeLinejoin="round" d="m9.5 12 2 2 3.5-3.5" />
    </svg>
  );
}

export function ComplianceIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 3.5h6M9 9l2 2 4-4" />
      <path strokeLinecap="round" d="M8 15h8" />
    </svg>
  );
}

export function SupportIcon({ className = base }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="3.5" />
      <path strokeLinecap="round" d="M5.5 5.5l3 3M18.5 5.5l-3 3M5.5 18.5l3-3M18.5 18.5l-3-3" />
    </svg>
  );
}

export const NAV_ICONS: Record<string, (props: IconProps) => React.JSX.Element> = {
  "/dashboard": DashboardIcon,
  "/dashboard/numbers": PhoneIcon,
  "/dashboard/call-flows": CallFlowIcon,
  "/dashboard/queues": QueueIcon,
  "/dashboard/messaging": MessagingIcon,
  "/dashboard/calls": CallLogIcon,
  "/dashboard/video": VideoIcon,
  "/dashboard/ai-insights": SparkleIcon,
  "/dashboard/voicemail": VoicemailIcon,
  "/dashboard/contacts": ContactsIcon,
  "/dashboard/reports": ReportsIcon,
  "/dashboard/billing": BillingIcon,
  "/dashboard/notifications": BellIcon,
  "/dashboard/business": BriefcaseIcon,
  "/dashboard/security": ShieldIcon,
  "/dashboard/compliance": ComplianceIcon,
  "/dashboard/settings": SettingsIcon,
  "/dashboard/support": SupportIcon,
};
