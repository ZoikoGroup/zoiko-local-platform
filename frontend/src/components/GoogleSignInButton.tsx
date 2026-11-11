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

// Google only issues an ID token through ITS OWN rendered button, so the
// visible "Continue with Google" row below is our own styled markup
// (matching the other sign-in rows in the design), and Google's real button
// is rendered on top of it at opacity 0. The user clicks what looks like
// our row but actually hits Google's genuine button - same security and
// same credential flow, just restyled.
export default function GoogleSignInButton({
  onCredential,
  className = "",
}: {
  onCredential: (credential: string) => void;
  /** Sizing/shape classes for the visible row (height, padding, radius). */
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
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

    const wrapper = wrapperRef.current;
    const overlay = containerRef.current;
    if (!wrapper) return;

    // Google caps its button at 400px wide, but our row can be wider (the
    // login card makes it ~434px). Rendering at 400px and leaving it at that
    // left the row's right-hand edge (and the chevron) un-clickable - "no
    // action" on a click there. So: render at min(400, row width), then
    // stretch the invisible overlay horizontally with a transform so it
    // covers the WHOLE row. Hit-testing follows transforms, so a click
    // anywhere on the row lands on Google's real button.
    const layout = () => {
      const rowWidth = Math.round(wrapper.getBoundingClientRect().width);
      if (!rowWidth) return;
      const width = Math.min(400, rowWidth);
      overlay.innerHTML = "";
      window.google?.accounts.id.renderButton(overlay, {
        theme: "outline",
        size: "large",
        width,
        text: "continue_with",
      });
      overlay.style.width = `${width}px`;
      overlay.style.transformOrigin = "left center";
      overlay.style.transform = width < rowWidth ? `scaleX(${rowWidth / width})` : "";
    };

    layout();
    // The card's width changes with the window, so keep the overlay in sync.
    let lastWidth = Math.round(wrapper.getBoundingClientRect().width);
    const observer = new ResizeObserver(() => {
      const w = Math.round(wrapper.getBoundingClientRect().width);
      if (w !== lastWidth) {
        lastWidth = w;
        layout();
      }
    });
    observer.observe(wrapper);
    return () => observer.disconnect();
  }, [scriptLoaded]);

  const row = (
    <>
      <svg width="20" height="20" viewBox="0 0 48 48" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.6 5.9c4.4-4.1 7-10.1 7-17.6z" />
        <path fill="#FBBC05" d="M10.5 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.2.8-4.7l-7.9-6.1C.9 16.4 0 20.1 0 24s.9 7.6 2.6 10.8l7.9-6.1z" />
        <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.9 2.3-8.3 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z" />
      </svg>
      <span className="flex-1 text-left">Continue with Google</span>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="text-slate-500">
        <path d="M9 6l6 6-6 6" />
      </svg>
    </>
  );

  const base =
    "w-full flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-900";

  if (!CLIENT_ID) {
    return (
      <div
        title="Google sign-in not configured"
        className={`${base} opacity-60 cursor-not-allowed ${className}`}
      >
        {row}
      </div>
    );
  }

  return (
    <div ref={wrapperRef} className="relative">
      <div className={`${base} ${className}`}>{row}</div>
      {/* Google's real button, invisible, covering the row (sized and
          stretched by the layout effect above). */}
      <div ref={containerRef} className="absolute left-0 top-0 h-full overflow-hidden opacity-0" />
    </div>
  );
}
