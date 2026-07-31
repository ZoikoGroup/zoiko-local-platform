"use client";

import { useEffect, useRef, useState } from "react";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential: string }) => void;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            options: { theme?: string; size?: string; width?: number; text?: string }
          ) => void;
        };
      };
    };
  }
}

const CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

export default function GoogleSignInButton({
  onCredential,
}: {
  onCredential: (credential: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scriptLoaded, setScriptLoaded] = useState(false);

  useEffect(() => {
    if (!CLIENT_ID) return;

    // Already fully loaded (e.g. from a prior mount) - don't re-add.
    if (window.google?.accounts?.id) {
      setScriptLoaded(true);
      return;
    }

    // React Strict Mode runs this effect twice in dev - the tag may
    // already exist from the first run but not have finished loading
    // yet (it's async). Attach a listener rather than assuming it's done.
    let script = document.getElementById("google-identity-script") as HTMLScriptElement | null;
    if (!script) {
      script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.id = "google-identity-script";
      script.async = true;
      script.defer = true;
      document.body.appendChild(script);
    }

    const handleLoad = () => setScriptLoaded(true);
    script.addEventListener("load", handleLoad);
    return () => script?.removeEventListener("load", handleLoad);
  }, []);

  useEffect(() => {
    if (!scriptLoaded || !CLIENT_ID || !containerRef.current || !window.google) return;

    window.google.accounts.id.initialize({
      client_id: CLIENT_ID,
      callback: (response) => onCredential(response.credential),
    });

    window.google.accounts.id.renderButton(containerRef.current, {
      theme: "outline",
      size: "large",
      width: 320,
      text: "continue_with",
    });
  }, [scriptLoaded, onCredential]);

  if (!CLIENT_ID) {
    return (
      <div className="w-full rounded-lg border border-dashed border-slate-300 bg-slate-50 text-slate-400 text-sm py-2.5 text-center">
        Google sign-in not configured
      </div>
    );
  }

  return <div ref={containerRef} className="w-full flex justify-center" />;
}
