import { describe, expect, it } from "vitest";
import { currentPageLabel } from "./nav";

describe("currentPageLabel", () => {
  it("returns Home for the exact dashboard root", () => {
    expect(currentPageLabel("/dashboard")).toBe("Home");
  });

  it("does not match the dashboard root (Home) for a nested path", () => {
    // /dashboard is a prefix of every other href, so Home's exact-match
    // check is required - otherwise it would win against every path.
    expect(currentPageLabel("/dashboard/video")).toBe("Video");
  });

  it("matches nested routes by prefix", () => {
    expect(currentPageLabel("/dashboard/numbers/123/settings")).toBe("My Numbers");
  });

  it("falls back to Dashboard for an unrecognized path", () => {
    expect(currentPageLabel("/some/other/route")).toBe("Dashboard");
  });

  it("falls back to Dashboard for null", () => {
    expect(currentPageLabel(null)).toBe("Dashboard");
  });
});
