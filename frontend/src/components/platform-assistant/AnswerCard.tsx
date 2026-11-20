"use client";

import Link from "next/link";
import type { AssistantMessage } from "@/lib/platformAssistant";
import EvidenceChip from "./EvidenceChip";
import FeedbackControl from "./FeedbackControl";
import PermissionDenied from "./PermissionDenied";
import ServiceOutage from "./ServiceOutage";

type Props = {
  message: AssistantMessage;
  feedbackDisabled: boolean;
  onFeedback: (messageId: string, rating: "helpful" | "not_helpful") => void;
};

export default function AnswerCard({ message, feedbackDisabled, onFeedback }: Props) {
  const blocks = message.content_blocks.map((block, index) => {
    if (block.type === "bullets") {
      return (
        <ul key={`${message.message_id}-block-${index}`} className="list-disc space-y-1 pl-5 text-sm text-slate-700">
          {block.items.map((item, itemIndex) => (
            <li key={`${message.message_id}-item-${itemIndex}`}>{item}</li>
          ))}
        </ul>
      );
    }
    return (
      <p key={`${message.message_id}-block-${index}`} className="text-sm leading-6 text-slate-700">
        {block.text}
      </p>
    );
  });

  const notice =
    message.answer_type === "permission_denied" ? (
      <PermissionDenied message={message.content_blocks[0]?.text ?? "Permission denied."} />
    ) : message.answer_type === "unknown" ? (
      <ServiceOutage message={message.content_blocks[0]?.text ?? "Service unavailable."} />
    ) : null;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-wide text-slate-500">
        <span>Assistant</span>
        <span aria-hidden="true">·</span>
        <span>{message.risk_tier}</span>
      </div>
      {notice ?? <div className="space-y-2">{blocks}</div>}
      {message.evidence.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2" aria-label="Evidence">
          {message.evidence.map((item) => (
            <EvidenceChip key={item.source_id} item={item} />
          ))}
        </div>
      )}
      {message.available_actions.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {message.available_actions.map((action) => (
            <Link
              key={action.destination_key}
              href={action.href}
              className="min-h-[44px] inline-flex items-center rounded-md bg-slate-900 px-3 text-xs font-medium text-white"
            >
              {action.label}
            </Link>
          ))}
        </div>
      )}
      <FeedbackControl messageId={message.message_id} disabled={feedbackDisabled} onFeedback={(rating) => onFeedback(message.message_id, rating)} />
    </div>
  );
}
