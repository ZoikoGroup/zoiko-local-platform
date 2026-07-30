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
    <div className="bg-white rounded-xl border border-slate-200 p-10 text-center max-w-xl mx-auto mt-10">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      <p className="text-sm text-slate-500 mt-2">{description}</p>
      <div className="inline-block mt-4 text-xs font-medium text-indigo-600 bg-indigo-50 rounded-full px-3 py-1">
        Coming in {stage}
      </div>
    </div>
  );
}
