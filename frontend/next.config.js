/** @type {import('next').NextConfig} */
const nextConfig = {
  // Bug ZL-1 (tester-reported): Google Sign-In "silently breaking" on
  // click. Real cause - not the OAuth consent-screen branding (that part
  // is a separate Google Cloud Console config, not code) - the browser's
  // default Cross-Origin-Opener-Policy blocks the popup window Google's
  // GSI script opens from calling back to this page via window.postMessage/
  // window.closed (console showed: "Cross-Origin-Opener-Policy policy
  // would block the window.postMessage call"), so the credential the
  // popup receives never reaches GoogleSignInButton's callback - the
  // button click does nothing, with no visible error. same-origin-allow-
  // popups keeps normal cross-origin isolation while explicitly permitting
  // this app's own popups (Google's OAuth popup, Stripe Checkout's popup
  // path if ever used) to communicate back - same-origin blocks it,
  // unsafe-none disables isolation entirely; this is the documented
  // middle ground for exactly this symptom.
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          {
            key: 'Cross-Origin-Opener-Policy',
            value: 'same-origin-allow-popups',
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
