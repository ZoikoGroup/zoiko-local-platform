"use client";

import { useState, forwardRef, type InputHTMLAttributes } from "react";

type PasswordInputProps = InputHTMLAttributes<HTMLInputElement> & {
  // Bug ZL-5 fix note: className stays on the <input> itself, completely
  // unchanged from how each of the 5 call sites already used it (full
  // visual styling - border/bg/text/placeholder color - preserved
  // pixel-for-pixel). The wrapping div only needs to reproduce whichever
  // LAYOUT classes (flex-1/min-w/w-full) made the original bare <input>
  // participate correctly in its parent's flex/block layout - without
  // this, the div (not the input) becomes the actual flex child and the
  // field could shrink/misalign since flex-1 etc. would be stuck on an
  // element nested one level too deep to matter. Defaults to "relative"
  // alone, which is already correct for a plain block-level field (the
  // 3 auth-page call sites) - only team/page.tsx's flex-row context needs
  // to pass this.
  containerClassName?: string;
};

// Bug ZL-5 (tester-reported) - every password field in the app was a bare
// type="password" input with no way to check what you typed before
// submitting. One shared component instead of repeating the toggle
// button/icon markup at each of the 5 call sites (login, signup,
// reset-password x2, staff team invite, business dashboard invite).
const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  function PasswordInput({ className, containerClassName, ...props }, ref) {
    const [visible, setVisible] = useState(false);

    return (
      <div className={`relative ${containerClassName ?? ""}`}>
        <input ref={ref} type={visible ? "text" : "password"} className={`${className ?? ""} pr-10`} {...props} />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide password" : "Show password"}
          tabIndex={-1}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition"
        >
          {visible ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 3l18 18" />
              <path d="M10.6 5.1A10.7 10.7 0 0112 5c6.5 0 10 7 10 7a15.6 15.6 0 01-3.3 4.2M6.6 6.6C4 8.3 2 12 2 12s3.5 7 10 7a9.7 9.7 0 004.4-1" />
              <path d="M9.9 9.9a3 3 0 004.2 4.2" />
            </svg>
          )}
        </button>
      </div>
    );
  }
);

export default PasswordInput;
