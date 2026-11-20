"use client";

import type { EvidenceItem } from "@/lib/platformAssistant";

export default function EvidenceChip({ item }: { item: EvidenceItem }) {
  return (
    <span
      className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-600"
      title={item.retrieved_at ? `Retrieved ${item.retrieved_at}` : undefined}
    >
      {item.title}
    </span>
  );
}
