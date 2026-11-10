"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/auth";

// The homepage is server-rendered, crawlable HTML for anonymous visitors
// and search engines. Someone who already has a session should still land
// in the app exactly as the old "/" redirect did - this keeps that
// behaviour without making the page itself client-only (which is what made
// "/" empty HTML before).
export default function HomeRedirect() {
  const router = useRouter();

  useEffect(() => {
    if (getToken()) router.replace("/dashboard");
  }, [router]);

  return null;
}
