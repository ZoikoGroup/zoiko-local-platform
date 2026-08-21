"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { getSubscription, type Subscription } from "@/lib/api";
import { getToken } from "@/lib/auth";

function daysLeft(trialEndsAt: string): number {
  const ms = new Date(trialEndsAt).getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / (1000 * 60 * 60 * 24)));
}

export default function TrialBanner() {
  const pathname = usePathname();
  const [subscription, setSubscription] = useState<Subscription | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    getSubscription(token)
      .then(setSubscription)
      .catch(() => {});
  }, []);

  // Already on the Billing page - the full plan/trial detail lives there,
  // a duplicate nudge above it would just be noise.
  if (pathname === "/dashboard/billing") return null;
  if (!subscription || subscription.status !== "trialing") return null;

  const days = subscription.trial_ends_at ? daysLeft(subscription.trial_ends_at) : null;

  return (
    <div className="bg-amber-50 border-b border-amber-200 px-6 py-2 flex items-center justify-between text-sm">
      <span className="text-amber-800">
        {days !== null
          ? `You're on a free trial — ${days} day${days === 1 ? "" : "s"} left.`
          : "You're on a free trial."}
      </span>
      <Link href="/dashboard/billing" className="font-medium text-amber-900 hover:text-amber-950 underline">
        Upgrade
      </Link>
    </div>
  );
}
