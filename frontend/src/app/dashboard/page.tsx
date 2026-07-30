import StatCard from "@/components/StatCard";

// NOTE: all numbers on this page are static placeholder data.
// Stage 1 (this build) only implements signup/login — numbers, calls,
// video, and AI features don't exist in the backend yet. This page
// exists to validate the corrected dashboard layout/navigation ahead
// of the stages that will make it real.

const RECENT_ACTIVITY = [
  { label: "Incoming Call", detail: "+1 (555) 123-4567", time: "2 min ago", status: "02:18" },
  { label: "Video Call", detail: "with +1 (555) 987-6543", time: "10 min ago", status: "Completed" },
  { label: "Number Purchased", detail: "+1 (754) 200-0123", time: "15 min ago", status: "Active" },
  { label: "AI Summary Generated", detail: "Call with +1 (555) 123-4567", time: "20 min ago", status: "Completed" },
  { label: "Voicemail Received", detail: "+1 (555) 111-2222", time: "25 min ago", status: "Transcribed" },
];

const TOP_COUNTRIES = [
  { country: "United States", value: "76 (53.5%)" },
  { country: "Canada", value: "18 (12.7%)" },
  { country: "United Kingdom", value: "12 (8.5%)" },
  { country: "Australia", value: "9 (6.3%)" },
  { country: "India", value: "7 (4.9%)" },
];

const AI_INSIGHTS = [
  { label: "Call Summary", detail: "AI generated 1,248 summaries this week", change: "28%" },
  { label: "Voicemail Intelligence", detail: "312 voicemails transcribed & summarized", change: "12%" },
  { label: "AI Receptionist", detail: "96 calls handled automatically this week", change: "18%" },
];

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Welcome back 👋</h2>
        <p className="text-sm text-slate-500">
          Here&apos;s what&apos;s happening with your communication platform.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <StatCard label="Total Calls" value="12,836" change="18.6%" />
        <StatCard label="Video Minutes" value="4,120" change="22.4%" />
        <StatCard label="Total Minutes" value="24,681" change="16.8%" />
        <StatCard label="Active Numbers" value="142" change="7.5%" />
        <StatCard label="AI Interactions" value="3,246" change="35.7%" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-slate-900 mb-4">Usage Overview</h3>
          <div className="h-56 flex items-center justify-center text-sm text-slate-400 border border-dashed border-slate-200 rounded-lg">
            Chart placeholder — wired to real data once Stage 2/3 (Numbers &amp; Calling) exist
          </div>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-slate-900">Recent Activity</h3>
            <button className="text-xs text-indigo-600 font-medium">View All</button>
          </div>
          <ul className="space-y-3">
            {RECENT_ACTIVITY.map((item) => (
              <li key={item.label + item.time} className="flex items-start justify-between text-sm">
                <div>
                  <div className="font-medium text-slate-800">{item.label}</div>
                  <div className="text-slate-500 text-xs">{item.detail}</div>
                </div>
                <div className="text-right">
                  <div className="text-xs text-slate-400">{item.time}</div>
                  <div className="text-xs text-emerald-600">{item.status}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-slate-900 mb-4">Top Countries (Numbers)</h3>
          <ul className="space-y-3">
            {TOP_COUNTRIES.map((row) => (
              <li key={row.country} className="flex items-center justify-between text-sm">
                <span className="text-slate-700">{row.country}</span>
                <span className="text-slate-500">{row.value}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-slate-900 mb-4">AI Insights</h3>
          <div className="grid sm:grid-cols-3 gap-4">
            {AI_INSIGHTS.map((insight) => (
              <div key={insight.label} className="rounded-lg border border-slate-100 p-4">
                <div className="text-sm font-medium text-slate-800">{insight.label}</div>
                <div className="text-xs text-slate-500 mt-1">{insight.detail}</div>
                <div className="text-xs text-emerald-600 mt-2">↗ {insight.change}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
