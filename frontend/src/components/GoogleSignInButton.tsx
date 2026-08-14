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

// Module-level, not component state: google.accounts.id.initialize() is
// meant to be called exactly once per page lifetime (per Google's own
// docs) - window.google persists across Next.js client-side navigation
// even though this component unmounts/remounts on every visit to a page
// that renders it (e.g. leaving /login and coming back), so component
// state alone would re-initialize on every mount. renderButton() is the
// part that must still run per mount, since each mount has a fresh
// container DOM node to render into.
//
// The one-time initialize() call registers ITS OWN callback closure,
// which would otherwise permanently capture whichever component instance
// mounted first (e.g. /login) - breaking /signup's GoogleSignInButton,
// since the two pages pass different onCredential handlers. Routing the
// callback through this module-level indirection instead, kept in sync
// with whichever instance is CURRENTLY mounted, decouples "initialize
// once" from "deliver the credential to whoever's showing the button now."
let gsiInitialized = false;
let activeCredentialHandler: ((credential: string) => void) | null = null;

export default function GoogleSignInButton({
  onCredential,
}: {
  onCredential: (credential: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // Lazy-initialized from the actual DOM state at mount, rather than set
  // via a synchronous setState call inside the effect below - avoids an
  // extra render and a redundant initialize() call when the script (or a
  // fully-initialized google.accounts.id) already exists from a prior mount.
  const [scriptLoaded, setScriptLoaded] = useState(
    () => typeof window !== "undefined" && !!window.google?.accounts?.id
  );
  // onCredential is re-created on every render of the parent (login/signup
  // pages don't memoize it) - synced into the module-level indirection
  // (see its docstring above) on every render, and cleared on unmount so
  // a stale handler from a page the user has since navigated away from
  // never fires.
  useEffect(() => {
    activeCredentialHandler = onCredential;
    return () => {
      if (activeCredentialHandler === onCredential) {
        activeCredentialHandler = null;
      }
    };
  }, [onCredential]);

  useEffect(() => {
    if (!CLIENT_ID || scriptLoaded) return;

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
  }, [scriptLoaded]);

  useEffect(() => {
    if (!scriptLoaded || !CLIENT_ID || !containerRef.current || !window.google) return;

    if (!gsiInitialized) {
      window.google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: (response) => activeCredentialHandler?.(response.credential),
      });
      gsiInitialized = true;
    }

    window.google.accounts.id.renderButton(containerRef.current, {
      theme: "outline",
      size: "large",
      width: 320,
      text: "continue_with",
    });
  }, [scriptLoaded]);

  if (!CLIENT_ID) {
    return (
      <div className="w-full rounded-lg border border-dashed border-slate-300 bg-slate-50 text-slate-400 text-sm py-2.5 text-center">
        Google sign-in not configured
      </div>
    );
  }

  return <div ref={containerRef} className="w-full flex justify-center" />;
}
