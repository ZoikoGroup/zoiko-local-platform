"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import AuthMockups from "@/components/AuthMockups";

const FEATURES = [
  {
    title: "Local numbers across borders",
    body: "Establish a local presence in key markets.",
    tone: "bg-emerald-50 border-emerald-100 text-slate-800",
    icon: <path d="M3 9l9-6 9 6v11a1 1 0 01-1 1H4a1 1 0 01-1-1V9zM9 21V12h6v9" />,
  },
  {
    title: "International calling & routing",
    body: "Connect and route calls intelligently.",
    tone: "bg-indigo-50 border-indigo-100 text-indigo-600",
    icon: (
      <path d="M22 16.9v3a2 2 0 01-2.2 2 19.8 19.8 0 01-8.6-3.1 19.5 19.5 0 01-6-6A19.8 19.8 0 012.1 4.2 2 2 0 014.1 2h3a2 2 0 012 1.7c.1 1 .4 1.9.7 2.8a2 2 0 01-.5 2.1L8.1 9.9a16 16 0 006 6l1.3-1.3a2 2 0 012.1-.4c.9.3 1.8.6 2.8.7a2 2 0 011.7 2z" />
    ),
  },
  {
    title: "AI Receptionist",
    body: "Help manage incoming conversations.",
    tone: "bg-emerald-50 border-emerald-100 text-slate-800",
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

// The shell is exactly one viewport tall (h-dvh + overflow hidden) so the page
// itself never scrolls. Every size below is a vh-based clamp, so the hero and
// the card shrink together on short laptop windows; `short:` (<=800px) and
// `tiny:` (<=700px) variants drop the least important pieces. As a last-resort
// safety net only, the FORM column scrolls internally if a taller page (e.g.
// signup on a very short window) still doesn't fit - never the whole page.
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
    <div className="h-dvh overflow-hidden flex bg-slate-50">
      {/* Hero panel - complementary marketing content, so an <aside>, not
          part of the <main> landmark below. Hidden below lg. */}
      <aside className="hidden lg:block lg:w-[60%] relative overflow-hidden">
        <img
          src="/auth/hero.png"
          alt=""
          aria-hidden="true"
          className="absolute inset-0 w-full h-full object-cover object-[28%_center]"
        />
        {/* Beige wash so the copy on the left stays readable over the photo */}
        <div className="absolute inset-0 bg-gradient-to-r from-slate-50 via-slate-50/80 to-transparent" />
        {/* Deep-green diagonal band behind the skyline, fading downward */}
        <div
          className="absolute top-0 left-[67%] w-[24%] h-[40%] bg-slate-800/90 [clip-path:polygon(38%_0,100%_0,100%_100%,0_100%)]"
          style={{ maskImage: "linear-gradient(to bottom, black 35%, transparent)" }}
        />

        <div className="relative h-full flex flex-col pt-[clamp(1.25rem,4.5vh,3.5rem)] pl-[clamp(2rem,4vw,4rem)] pr-8">
          <div>
            <img src="/wordmark.svg" alt="Zoiko Local" className="h-[clamp(1.6rem,4.9vh,3.5rem)] w-auto" />
            <p className="mt-[1.4vh] text-[clamp(9px,1.2vh,12px)] font-semibold tracking-[0.25em] text-slate-800 uppercase">
              Sound local. Reach anywhere.
            </p>
          </div>

          <div className="mt-[2.6vh] max-w-[34rem]">
            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-4 py-[0.55vh] text-[clamp(11px,1.6vh,14px)] font-semibold text-slate-900">
              <span aria-hidden="true">&rarr;</span> Switch from Skype
            </span>
            <h2 className="mt-[1.9vh] text-[clamp(1.9rem,6.3vh,4.25rem)] font-bold leading-[1.02] tracking-tight text-slate-900">
              Be local where
              <br />
              <span className="text-indigo-600">it matters.</span>
            </h2>
            <p className="mt-[1.5vh] text-[clamp(0.78rem,2.05vh,1.15rem)] leading-snug text-slate-500 max-w-[30rem]">
              Global phone numbers for cross-border life and business. Call, connect, and manage your
              communications from one place.
            </p>

            <ul className="mt-[2vh] space-y-[clamp(0.3rem,1.2vh,0.85rem)]">
              {FEATURES.map((f) => (
                <li key={f.title} className="flex items-center gap-3">
                  <span
                    className={`w-[clamp(1.75rem,4.6vh,3rem)] h-[clamp(1.75rem,4.6vh,3rem)] rounded-xl flex items-center justify-center shrink-0 border ${f.tone}`}
                  >
                    <svg
                      width="55%"
                      height="55%"
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
                  <div className="leading-tight">
                    <div className="font-semibold text-slate-900 text-[clamp(11px,1.85vh,16px)]">{f.title}</div>
                    <div className="text-slate-500 text-[clamp(10px,1.5vh,13px)]">{f.body}</div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Product previews - bottom-left, overlapping the photo like the design */}
        <div className="absolute left-[2.5vw] bottom-[4.2vh] xshort:hidden">
          <AuthMockups />
        </div>

        <p className="absolute left-[clamp(2rem,4vw,4rem)] bottom-[1.2vh] text-[clamp(9px,1.2vh,11px)] text-slate-500">
          Product previews are illustrative. Number availability varies by market.
        </p>
      </aside>

      {/* Form panel - the page's actual content */}
      <main className="flex-1 min-h-0 min-w-0 overflow-y-auto bg-slate-100/70">
        <div className="min-h-full flex items-center justify-center p-[clamp(0.5rem,2vh,1.5rem)]">
          <div className="w-full max-w-[500px] bg-white rounded-2xl shadow-xl shadow-slate-300/40 border border-slate-100 overflow-hidden">
            <nav aria-label="Account" className="grid grid-cols-3 border-b border-slate-100">
              {TABS.map((t) => {
                const active = t.match.includes(pathname);
                return (
                  <Link
                    key={t.href}
                    href={t.href}
                    aria-current={active ? "page" : undefined}
                    className={`relative py-[clamp(0.55rem,1.9vh,1.1rem)] text-center text-sm font-semibold transition ${
                      active ? "text-slate-900" : "text-slate-600 hover:text-slate-900"
                    }`}
                  >
                    {t.label}
                    {active && (
                      <span className="absolute left-4 right-4 bottom-0 h-[3px] rounded-t bg-indigo-600" />
                    )}
                  </Link>
                );
              })}
            </nav>

            <div className="px-[clamp(1.25rem,2.4vw,2rem)] pt-[clamp(0.5rem,2vh,1.25rem)] pb-[clamp(0.4rem,1.2vh,0.9rem)]">
              <div className="flex flex-col items-center text-center mb-[clamp(0.35rem,1.3vh,1rem)]">
                <img
                  src="/logo-icon.svg"
                  alt=""
                  aria-hidden="true"
                  className="h-[clamp(2rem,5.6vh,4rem)] w-auto rounded-xl tiny:hidden"
                />
                <img
                  src="/wordmark.svg"
                  alt="Zoiko Local"
                  className="h-[clamp(0.95rem,2.4vh,1.5rem)] w-auto mt-[0.8vh]"
                />
                <h1 className="text-[clamp(1.2rem,3.6vh,1.9rem)] font-bold leading-tight text-slate-900 mt-[1.2vh]">
                  {title}
                </h1>
                <p className="text-[clamp(11px,1.7vh,14px)] text-slate-500 mt-[0.3vh] tiny:hidden">{subtitle}</p>
              </div>

              {children}

              <p className="mt-[clamp(0.4rem,1.4vh,0.9rem)] flex items-center justify-center gap-1.5 text-[11px] text-slate-500">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <rect x="4" y="11" width="16" height="10" rx="2" />
                  <path d="M8 11V7a4 4 0 018 0v4" />
                </svg>
                Secure account access &middot;{" "}
                <span className="font-semibold text-slate-800">Additional verification may be required</span>
              </p>
            </div>

            <footer className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-[clamp(1.25rem,2.4vw,2rem)] py-[clamp(0.4rem,1.4vh,0.85rem)] text-[11px] text-slate-500 border-t border-slate-100">
              <div className="flex gap-4">
                <Link href="/status" className="hover:text-slate-900">Status</Link>
                <a href="mailto:support@zoikolocal.com" className="hover:text-slate-900">Help</a>
              </div>
              <span>&copy; {new Date().getFullYear()} Zoiko Tech Inc.</span>
            </footer>
          </div>
        </div>
      </main>
    </div>
  );
}
