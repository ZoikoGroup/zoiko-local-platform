"use client";

import type { PlanChangePreview } from "@/lib/api";

// Friendly labels for the entitlement keys most likely to show up in a
// diff - falls back to a humanized version of the raw key (e.g.
// "routing.advanced" -> "routing advanced") for anything not listed here,
// so a new key added on the backend never renders as literally blank.
const ENTITLEMENT_LABELS: Record<string, string> = {
  "routing.advanced": "Advanced call routing & escalation",
  "routing.shared": "Shared call handling / ring groups",
  "routing.transfer": "Call transfer",
  "routing.multi_market": "Multi-market routing",
  "routing.business_hours": "Business-hours routing",
  "reporting.advanced": "Advanced analytics & reporting",
  "reporting.business": "Business reporting",
  "analytics.cross_market": "Cross-market analytics",
  "team.enabled": "Team members",
  "team.roles.standard": "Owner/Admin/Member roles",
  "admin.advanced_roles": "Advanced operational roles",
  "admin.multi_market": "Multi-market administration",
  "messaging.shared_team": "Shared team messaging",
  "developer.api.scope": "API access level",
  "developer.webhooks.scope": "Webhook access level",
  "number.assignment.team": "Assign numbers to team members",
};

function friendlyKey(key: string): string {
  return ENTITLEMENT_LABELS[key] ?? key.replace(/[._]/g, " ");
}

export default function PlanChangePreviewModal({
  preview,
  targetPlanName,
  priceLabel,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  preview: PlanChangePreview;
  targetPlanName: string;
  priceLabel: string;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const isDowngrade = preview.direction === "downgrade";
  const { resource_impact: impact, ai_receptionist_included_minutes: ai } = preview;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div className="w-full max-w-md rounded-xl bg-white shadow-xl p-6 space-y-4 max-h-[85vh] overflow-y-auto">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">
            {isDowngrade ? "Move to" : "Upgrade to"} {targetPlanName}
          </h3>
          <p className="text-sm text-slate-500 mt-0.5">
            {priceLabel}
            {isDowngrade && preview.effective_at
              ? ` - takes effect ${new Date(preview.effective_at).toLocaleDateString()}`
              : " - takes effect immediately"}
          </p>
        </div>

        {preview.entitlement_diff.gained.length > 0 && (
          <div>
            <p className="text-xs font-medium text-emerald-700 uppercase tracking-wide mb-1">You&apos;ll gain</p>
            <ul className="text-sm text-slate-700 space-y-0.5">
              {preview.entitlement_diff.gained.map((key) => (
                <li key={key} className="flex items-start gap-1.5">
                  <span className="text-emerald-600">+</span>
                  <span className="capitalize">{friendlyKey(key)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {preview.entitlement_diff.lost.length > 0 && (
          <div>
            <p className="text-xs font-medium text-red-700 uppercase tracking-wide mb-1">You&apos;ll lose</p>
            <ul className="text-sm text-slate-700 space-y-0.5">
              {preview.entitlement_diff.lost.map((key) => (
                <li key={key} className="flex items-start gap-1.5">
                  <span className="text-red-600">-</span>
                  <span className="capitalize">{friendlyKey(key)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {ai.current !== ai.target && (
          <p className="text-sm text-slate-600 bg-slate-50 rounded-lg px-3 py-2">
            AI Receptionist minutes: {ai.current} → {ai.target} / month
          </p>
        )}

        {impact && (impact.numbers_over_target_limit > 0 || impact.team_capability_lost) && (
          <div className="text-sm text-amber-800 bg-amber-50 rounded-lg px-3 py-2 space-y-1">
            <p className="font-medium">Before you switch</p>
            {impact.numbers_over_target_limit > 0 && (
              <p>
                You own {impact.numbers_owned} numbers, {impact.numbers_over_target_limit} more than{" "}
                {targetPlanName} includes. Already-purchased numbers stay yours - nothing is released automatically.
              </p>
            )}
            {impact.team_capability_lost && (
              <p>
                {targetPlanName} is single-user. Your {impact.team_seats_used} team members keep their accounts, but
                lose team access until you upgrade again.
              </p>
            )}
          </div>
        )}

        {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

        <div className="flex gap-2 pt-2">
          <button
            onClick={onConfirm}
            disabled={busy}
            className="flex-1 text-sm font-medium rounded-lg px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
          >
            {busy ? "Working..." : isDowngrade ? "Schedule downgrade" : "Confirm upgrade"}
          </button>
          <button
            onClick={onClose}
            disabled={busy}
            className="flex-1 text-sm font-medium rounded-lg px-4 py-2 border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
