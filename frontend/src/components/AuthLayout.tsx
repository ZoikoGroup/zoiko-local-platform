"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const FEATURES = [
  {
    title: "Local numbers across borders",
    body: "Establish a local presence in key markets.",
    icon: (
      <path d="M3 9l9-6 9 6v11a1 1 0 01-1 1H4a1 1 0 01-1-1V9zM9 21V12h6v9" />
    ),
  },
  {
    title: "International calling & routing",
    body: "Connect and route calls intelligently.",
    icon: (
      <path d="M22 16.9v3a2 2 0 01-2.2 2 19.8 19.8 0 01-8.6-3.1 19.5 19.5 0 01-6-6A19.8 19.8 0 012.1 4.2 2 2 0 014.1 2h3a2 2 0 012 1.7c.1 1 .4 1.9.7 2.8a2 2 0 01-.5 2.1L8.1 9.9a16 16 0 006 6l1.3-1.3a2 2 0 012.1-.4c.9.3 1.8.6 2.8.7a2 2 0 011.7 2z" />
    ),
  },
  {
    title: "AI Receptionist",
    body: "Help manage incoming conversations.",
    icon: (
      <>
        <rect x="4" y="8" width="16" height="12" rx="2" />
        <path d="M12 8V4M9 14h.01M15 14h.01" />
      </>
    ),
  },
];

const TABS = [
  { label: "Login", href: "/login", match: ["/login"] },
  { label: "Register", href: "/signup", match: ["/signup"] },
  { label: "Reset Password", href: "/forgot-password", match: ["/forgot-password", "/reset-password"] },
];

export default function AuthLayout({
  children,
  title,
  subtitle,
}: {
  children: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen flex flex-col lg:flex-row bg-slate-50">
      {/* Hero panel - complementary marketing content, so an <aside>, not
          part of the <main> landmark below. Hidden below lg. */}
      <aside className="hidden lg:flex lg:w-[58%] relative overflow-hidden flex-col justify-between p-12 xl:p-16">
        <img
          src="/auth/hero.png"
          alt=""
          aria-hidden="true"
          className="absolute inset-0 w-full h-full object-cover object-[30%_center]"
        />
        {/* Beige wash so the copy on the left stays readable over the photo */}
        <div className="absolute inset-0 bg-gradient-to-r from-slate-50 via-slate-50/85 to-transparent" />

        <div className="relative">
          <img src="/wordmark.svg" alt="Zoiko Local" className="h-10 w-auto" />
          <p className="mt-3 text-[11px] font-semibold tracking-[0.25em] text-slate-800 uppercase">
            Sound local. Reach anywhere.
          </p>
        </div>

        <div className="relative max-w-lg">
          <span className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-4 py-1.5 text-sm font-medium text-slate-900">
            <span aria-hidden="true">&rarr;</span> Switch from Skype
          </span>
          <h2 className="mt-8 text-5xl xl:text-6xl font-bold leading-[1.05] text-slate-900">
            Be local where
            <br />
            <span className="text-indigo-600">it matters.</span>
          </h2>
          <p className="mt-6 text-lg text-slate-500 max-w-md">
            Global phone numbers for cross-border life and business. Call, connect, and manage your
            communications from one place.
          </p>

          <ul className="mt-8 space-y-5">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex items-center gap-4">
                <span className="w-12 h-12 rounded-xl bg-emerald-50 text-slate-800 flex items-center justify-center shrink-0 border border-emerald-100">
                  <svg
                    width="22"
                    height="22"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    {f.icon}
                  </svg>
                </span>
                <div>
                  <div className="font-semibold text-slate-900">{f.title}</div>
                  <div className="text-sm text-slate-500">{f.body}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs text-slate-500">
          Product previews are illustrative. Number availability varies by market.
        </p>
      </aside>

      {/* Form panel - the page's actual content */}
      <main className="flex-1 flex items-center justify-center p-4 sm:p-8 lg:bg-slate-100/60">
        <div className="w-full max-w-[460px] bg-white rounded-2xl shadow-xl shadow-slate-300/40 border border-slate-100 overflow-hidden">
          <nav aria-label="Account" className="grid grid-cols-3 border-b border-slate-100">
            {TABS.map((t) => {
              const active = t.match.includes(pathname);
              return (
                <Link
                  key={t.href}
                  href={t.href}
                  aria-current={active ? "page" : undefined}
                  className={`relative py-4 text-center text-sm font-semibold transition ${
                    active ? "text-slate-900" : "text-slate-600 hover:text-slate-900"
                  }`}
                >
                  {t.label}
                  {active && (
                    <span className="absolute left-3 right-3 bottom-0 h-[3px] rounded-t bg-indigo-600" />
                  )}
                </Link>
              );
            })}
          </nav>

          <div className="px-6 sm:px-8 pt-7 pb-6">
            <div className="flex flex-col items-center text-center mb-6">
              <img src="/logo-icon.svg" alt="" aria-hidden="true" className="w-14 h-14 rounded-xl" />
              <img src="/wordmark.svg" alt="Zoiko Local" className="h-6 w-auto mt-3" />
              <h1 className="text-2xl font-bold text-slate-900 mt-5">{title}</h1>
              <p className="text-sm text-slate-500 mt-1">{subtitle}</p>
            </div>

            {children}

            <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-slate-500">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <rect x="4" y="11" width="16" height="10" rx="2" />
                <path d="M8 11V7a4 4 0 018 0v4" />
              </svg>
              Secure account access &middot;{" "}
              <span className="font-semibold text-slate-800">Additional verification may be required</span>
            </p>
          </div>

          <footer className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-6 sm:px-8 py-4 text-xs text-slate-500 border-t border-slate-100">
            <div className="flex gap-4">
              <Link href="/status" className="hover:text-slate-900">Status</Link>
              <a href="mailto:support@zoikolocal.com" className="hover:text-slate-900">Help</a>
            </div>
            <span>&copy; {new Date().getFullYear()} Zoiko Tech Inc.</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
