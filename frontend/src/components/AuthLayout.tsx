import Logo from "@/components/Logo";

const FEATURES = [
  "Local numbers in 8+ countries",
  "AI-powered voicemail & call summaries",
  "Built-in video calling",
  "Business accounts with team roles",
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
  return (
    <div className="min-h-screen flex bg-slate-50">
      {/* Branded side panel - hidden on small screens. Complementary
          marketing content, not the page's main content, so it's an
          <aside>, not part of the <main> landmark below. */}
      <aside className="hidden lg:flex lg:w-[45%] bg-gradient-to-br from-indigo-700 via-indigo-600 to-slate-800 text-white flex-col justify-between p-12 relative overflow-hidden">
        <div className="absolute inset-0 opacity-10 bg-[radial-gradient(circle_at_top_right,white,transparent_60%)]" />

        <div className="relative flex items-center gap-2">
          <Logo size={36} />
          <div>
            <div className="font-semibold leading-tight">Zoiko Local</div>
            <div className="text-xs text-indigo-200 leading-tight">Communications Platform</div>
          </div>
        </div>

        <div className="relative space-y-6">
          <h2 className="text-3xl font-semibold leading-tight max-w-sm">
            Sound local. Stay close.
          </h2>
          <ul className="space-y-3">
            {FEATURES.map((f) => (
              <li key={f} className="flex items-start gap-2 text-sm text-indigo-100">
                <span className="mt-0.5 w-5 h-5 rounded-full bg-white/15 flex items-center justify-center text-xs shrink-0">
                  ✓
                </span>
                {f}
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs text-indigo-300">
          &copy; {new Date().getFullYear()} Zoiko Group Inc. &middot;{" "}
          <a href="/status" className="hover:text-white underline underline-offset-2">
            System status
          </a>
        </p>
      </aside>

      {/* Form panel - the page's actual content */}
      <main className="flex-1 flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-2 mb-8 justify-center">
            <Logo size={36} />
            <div>
              <div className="font-semibold text-slate-900 leading-tight">Zoiko Local</div>
              <div className="text-xs text-slate-500 leading-tight">Communications Platform</div>
            </div>
          </div>

          <div className="bg-white rounded-2xl shadow-xl shadow-slate-200/60 border border-slate-100 p-8">
            <h1 className="text-xl font-semibold text-slate-900 mb-1">{title}</h1>
            <p className="text-sm text-slate-500 mb-6">{subtitle}</p>
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}
