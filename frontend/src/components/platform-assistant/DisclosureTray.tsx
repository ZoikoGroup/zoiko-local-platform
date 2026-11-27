"use client";

export default function DisclosureTray({ disclosure, notice }: { disclosure: string; notice: string }) {
  return (
    <details className="rounded-lg border border-slate-200 bg-white p-3">
      <summary className="min-h-[44px] cursor-pointer text-sm font-medium text-slate-800 flex items-center">
        AI identity and read-only scope
      </summary>
      <p className="mt-2 text-xs leading-5 text-slate-600">{disclosure}</p>
      <p className="mt-1 text-xs leading-5 text-slate-600">{notice}</p>
    </details>
  );
}
