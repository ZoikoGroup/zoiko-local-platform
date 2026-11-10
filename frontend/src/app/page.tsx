import type { Metadata } from "next";
import Link from "next/link";
import HomeRedirect from "@/components/HomeRedirect";

// Approved homepage SEO copy (Homepage SEO Metadata spec). Keep these
// strings identical to the spec - they are the approved wording, with
// length limits (title <= 60, description <= 155) already checked there.
const TITLE = "Zoiko Local | Local Phone Numbers & AI Receptionist";
const DESCRIPTION =
  "Zoiko Local offers local phone numbers for business and personal use, voice and video calling, voicemail and an AI Receptionist. Check availability.";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://zoikolocal.com";
const IMAGE_ALT = "Zoiko Local - local phone numbers, voice and video calling, and an AI Receptionist";

export const metadata: Metadata = {
  // `absolute` so the root layout's title template (if one is ever added)
  // can't alter the approved homepage title.
  title: { absolute: TITLE },
  description: DESCRIPTION,
  // Absolute, with the trailing slash exactly as in the spec
  // (https://zoikolocal.com/) - a relative "/" resolved to no trailing slash.
  alternates: { canonical: `${SITE_URL}/` },
  openGraph: {
    type: "website",
    url: `${SITE_URL}/`,
    title: TITLE,
    description: DESCRIPTION,
    siteName: "Zoiko Local",
    images: [{ url: `${SITE_URL}/opengraph-image`, width: 1200, height: 630, alt: IMAGE_ALT }],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    images: [{ url: `${SITE_URL}/twitter-image`, alt: IMAGE_ALT }],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
};

// Organization, WebSite and WebPage as one linked @graph. Only facts that
// are actually true and known are included (name, URL, logo, description) -
// no address, phone, or social profiles, since none were supplied and
// structured data must not assert unverified entity details.
const jsonLd = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${SITE_URL}/#organization`,
      name: "Zoiko Local",
      url: `${SITE_URL}/`,
      logo: `${SITE_URL}/logo-icon.svg`,
    },
    {
      "@type": "WebSite",
      "@id": `${SITE_URL}/#website`,
      url: `${SITE_URL}/`,
      name: "Zoiko Local",
      inLanguage: "en",
      publisher: { "@id": `${SITE_URL}/#organization` },
    },
    {
      "@type": "WebPage",
      "@id": `${SITE_URL}/#webpage`,
      url: `${SITE_URL}/`,
      name: TITLE,
      description: DESCRIPTION,
      inLanguage: "en",
      isPartOf: { "@id": `${SITE_URL}/#website` },
      about: { "@id": `${SITE_URL}/#organization` },
    },
  ],
};

const TOPICS = [
  {
    title: "Local phone numbers",
    body: "Establish a local presence in supported markets with business and personal numbers.",
  },
  {
    title: "Voice and video calling",
    body: "Call and meet over video from the same account, wherever you are.",
  },
  {
    title: "Intelligent voicemail",
    body: "Voicemail you can read, with AI-generated summaries of what was said.",
  },
  {
    title: "AI Receptionist",
    body: "AI-powered call handling that answers, qualifies and routes incoming calls.",
  },
];

export default function HomePage() {
  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <script
        type="application/ld+json"
        // "<" escaped per the Next.js JSON-LD guide, so no string in the
        // payload can ever close the script tag.
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c") }}
      />
      <HomeRedirect />

      <header className="max-w-6xl w-full mx-auto flex items-center justify-between px-6 py-5">
        <Link href="/" aria-label="Zoiko Local home">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/wordmark.svg" alt="Zoiko Local" className="h-8 w-auto" />
        </Link>
        <nav aria-label="Primary" className="flex items-center gap-5 text-sm font-semibold">
          <Link href="/pricing" className="text-slate-700 hover:text-slate-900">
            Pricing
          </Link>
          <Link href="/login" className="text-slate-700 hover:text-slate-900">
            Log in
          </Link>
        </nav>
      </header>

      <main className="flex-1">
        <section className="max-w-4xl mx-auto px-6 pt-14 pb-16 text-center">
          <h1 className="text-4xl sm:text-5xl font-bold leading-[1.1] text-slate-900">
            Local Phone Numbers for Business and Everyday Life
          </h1>
          <p className="mt-5 text-xl sm:text-2xl font-semibold text-indigo-600">
            Voice, Video and an AI Receptionist in One Platform
          </p>
          <p className="mt-6 text-lg text-slate-500 max-w-3xl mx-auto">
            Zoiko Local brings local phone numbers, business calling, video meetings, intelligent
            voicemail and AI-powered call handling into one connected communications platform.
            Establish a local presence in supported markets, manage conversations and stay connected
            across borders.
          </p>
          <div className="mt-9 flex flex-col sm:flex-row items-center justify-center gap-3">
            <Link
              href="/signup"
              className="w-full sm:w-auto rounded-lg bg-slate-800 hover:bg-slate-900 text-white px-7 py-3.5 text-base font-semibold shadow-md shadow-slate-800/25 transition"
            >
              Get a Local Number
            </Link>
            <Link
              href="/pricing#coverage"
              className="w-full sm:w-auto rounded-lg border border-slate-300 bg-white hover:bg-slate-50 text-slate-900 px-7 py-3.5 text-base font-semibold transition"
            >
              Check Global Coverage
            </Link>
          </div>
        </section>

        <section aria-labelledby="platform-heading" className="max-w-6xl mx-auto px-6 pb-20">
          <h2 id="platform-heading" className="sr-only">
            What Zoiko Local includes
          </h2>
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {TOPICS.map((t) => (
              <li key={t.title} className="rounded-2xl border border-slate-200 bg-white p-6">
                <h3 className="font-semibold text-slate-900">{t.title}</h3>
                <p className="mt-2 text-sm text-slate-500">{t.body}</p>
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="border-t border-slate-200 px-6 py-6 text-xs text-slate-500">
        <div className="max-w-6xl mx-auto flex flex-wrap items-center justify-between gap-3">
          <span>&copy; {new Date().getFullYear()} Zoiko Tech Inc.</span>
          <div className="flex gap-4">
            <Link href="/pricing" className="hover:text-slate-900">Pricing</Link>
            <Link href="/status" className="hover:text-slate-900">Status</Link>
            <a href="mailto:support@zoikolocal.com" className="hover:text-slate-900">Help</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
