"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import Topbar from "@/components/Topbar";
import TrialBanner from "@/components/TrialBanner";
import { ApiError, getCurrentUser, getSubscription, type Subscription, type User } from "@/lib/api";
import { getToken, clearToken } from "@/lib/auth";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }

    getCurrentUser(token)
      .then(setUser)
      .catch((err) => {
        // Only a real 401 means the token is actually invalid. Anything
        // else (a network hiccup, or React Strict Mode's dev-only double
        // effect invocation racing/aborting the first call) must not log
        // out someone who is genuinely still authenticated.
        if (err instanceof ApiError && err.status === 401) {
          clearToken();
          router.replace("/login");
        }
      })
      .finally(() => setChecking(false));

    // Fetched once here (not separately in Sidebar/TrialBanner) since both
    // need the same trial-vs-paid status - was previously two independent
    // fetches (TrialBanner had its own), now one shared value passed down.
    getSubscription(token)
      .then(setSubscription)
      .catch(() => {
        // Sidebar/TrialBanner both treat a null subscription as "don't
        // lock/show anything" rather than fail the whole dashboard over a
        // transient billing-service hiccup.
      });
  }, [router]);

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-500 text-sm">
        Loading...
      </div>
    );
  }

  // SCROLL OWNERSHIP — why h-screen + overflow-hidden, not min-h-screen:
  //
  // This shell used to be `min-h-screen`, which lets it grow as tall as its
  // content. A child's `overflow-y-auto` only scrolls when its height is
  // actually constrained, so on a tall page nothing here scrolled at all —
  // the browser window scrolled instead, dragging the sidebar up out of
  // view along with the content.
  //
  // Pinning the shell to exactly the viewport height (and clipping its own
  // overflow) gives the two regions independent scrollbars:
  //   - <aside> (Sidebar) is a flex child, so it stretches to full height;
  //     its <nav> already has overflow-y-auto and now scrolls internally.
  //   - <main> is the only thing that scrolls the beige content area.
  // The Topbar and TrialBanner sit outside <main>, so they stay put.
  return (
    <div className="h-screen flex overflow-hidden bg-slate-50">
      <Sidebar subscription={subscription} />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Topbar user={user} />
        <TrialBanner subscription={subscription} />
        {/* min-h-0 is load-bearing, not cosmetic: `flex-1` is
            `flex:1 1 0%` but leaves `min-height:auto`, which in a column
            flex container refuses to shrink below the content's
            min-content height. Without it, <main> grows to fit its
            children and overflow-y-auto never engages. */}
        <main className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
