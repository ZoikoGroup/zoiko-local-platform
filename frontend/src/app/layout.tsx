import type { Metadata } from "next";
import { Poppins, Inter, Geist_Mono } from "next/font/google";
import "./globals.css";

// Display face for headings — the geometric sans used across the
// zoikolocal.com marketing site. See the README note: the exact licensed
// brand font can't be identified from a screenshot, and Poppins is the
// closest free match. Swap the import + variable here if you have the real
// one; nothing else needs to change.
const heading = Poppins({
  variable: "--font-heading",
  weight: ["400", "500", "600", "700"],
  subsets: ["latin"],
  display: "swap",
});

// Body/UI face — neutral grotesque, readable at the 12–14px sizes this
// dashboard uses heavily.
const body = Inter({
  variable: "--font-body",
  subsets: ["latin"],
  display: "swap",
});

// Kept as-is. Used by 46 places across the dashboard for phone numbers,
// IDs, TOTP codes and API paths — all cases where a monospace face is
// carrying meaning, not decoration.
const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Zoiko Local",
  description: "AI-native cross-border communications platform.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${heading.variable} ${body.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
