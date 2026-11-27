"use client";

type Props = {
  messageId: string;
  disabled: boolean;
  onFeedback: (rating: "helpful" | "not_helpful") => void;
};

export default function FeedbackControl({ messageId, disabled, onFeedback }: Props) {
  return (
    <div className="mt-3 flex gap-2" aria-label={`Feedback for ${messageId}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onFeedback("helpful")}
        className="min-h-[44px] rounded-md border border-slate-300 px-3 text-xs text-slate-700 disabled:opacity-50"
      >
        Helpful
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onFeedback("not_helpful")}
        className="min-h-[44px] rounded-md border border-slate-300 px-3 text-xs text-slate-700 disabled:opacity-50"
      >
        Not helpful
      </button>
    </div>
  );
}
