export default function ComingSoon({
  title,
  description,
  stage,
}: {
  title: string;
  description: string;
  stage: string;
}) {
  return (
    <div className="bg-white rounded-2xl border border-slate-200 p-10 text-center max-w-xl mx-auto mt-10 shadow-sm shadow-slate-200/50">
      <div className="w-12 h-12 mx-auto rounded-full bg-indigo-50 flex items-center justify-center mb-4">
        <svg className="w-6 h-6 text-indigo-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="12" cy="12" r="9" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 7v5l3.5 2" />
        </svg>
      </div>
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      <p className="text-sm text-slate-500 mt-2 leading-relaxed">{description}</p>
      <div className="inline-block mt-4 text-xs font-medium text-indigo-600 bg-indigo-50 rounded-full px-3 py-1.5">
        Coming in {stage}
      </div>
    </div>
  );
}
