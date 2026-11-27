"use client";

export default function ContextChip({ module, role, tenantScope }: { module: string; role: string; tenantScope: string }) {
  return (
    <div className="flex flex-wrap gap-2" aria-label="Assistant context">
      <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-700">{module}</span>
      <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-700">{role}</span>
      <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-700">{tenantScope}</span>
    </div>
  );
}
