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

  return (
    <div className="min-h-screen flex bg-slate-50">
      <Sidebar subscription={subscription} />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar user={user} />
        <TrialBanner subscription={subscription} />
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
