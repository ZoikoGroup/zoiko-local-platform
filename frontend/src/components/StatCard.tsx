export default function StatCard({
  label,
  value,
  change,
}: {
  label: string;
  value: string;
  change: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 hover:shadow-md hover:shadow-slate-200/60 hover:border-slate-300 transition">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="text-2xl font-semibold text-slate-900 mt-1.5 tracking-tight">{value}</div>
      <div className="flex items-center gap-1 text-xs text-emerald-600 mt-2 font-medium">
        <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M9 6h9v9" />
        </svg>
        {change} vs last week
      </div>
    </div>
  );
}
