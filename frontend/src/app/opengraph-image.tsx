import { ImageResponse } from "next/og";

// Branded 1200x630 social-sharing image. Generated (not a stored PNG) so it
// stays in sync with the brand colours in globals.css; swap for a
// designer-made PNG named opengraph-image.png in this folder at any time -
// the file convention picks either.
export const alt = "Zoiko Local - local phone numbers, voice and video calling, and an AI Receptionist";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "#f6f2ec",
          color: "#073b37",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ width: 20, height: 20, borderRadius: 10, background: "#e85d3c" }} />
          <div style={{ fontSize: 44, fontWeight: 800, letterSpacing: -1, color: "#0a4f4a" }}>
            ZOIKO LOCAL
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: 76, fontWeight: 800, lineHeight: 1.05, letterSpacing: -2 }}>
            Local Phone Numbers
          </div>
          <div style={{ fontSize: 76, fontWeight: 800, lineHeight: 1.05, letterSpacing: -2, color: "#e85d3c" }}>
            &amp; AI Receptionist
          </div>
          <div style={{ marginTop: 28, fontSize: 32, color: "#6e6b60" }}>
            Voice, video and AI call handling in one platform
          </div>
        </div>

        <div style={{ height: 12, borderRadius: 6, background: "#0a4f4a", display: "flex" }} />
      </div>
    ),
    size
  );
}
