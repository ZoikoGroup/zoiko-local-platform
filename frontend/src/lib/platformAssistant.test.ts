import { describe, expect, it } from "vitest";
import { moduleForPath } from "./platformAssistant";

describe("moduleForPath", () => {
  it("maps dashboard routes to platform assistant modules", () => {
    expect(moduleForPath("/dashboard")).toBe("overview");
    expect(moduleForPath("/dashboard/calls")).toBe("calls");
    expect(moduleForPath("/dashboard/call-flows")).toBe("receptionist");
    expect(moduleForPath("/dashboard/numbers")).toBe("numbers");
    expect(moduleForPath("/dashboard/billing")).toBe("billing");
    expect(moduleForPath("/dashboard/messaging")).toBe("inbox");
    expect(moduleForPath("/dashboard/voicemail")).toBe("inbox");
    expect(moduleForPath("/dashboard/ai-insights")).toBe("analytics");
    expect(moduleForPath("/dashboard/reports")).toBe("analytics");
    expect(moduleForPath("/dashboard/video")).toBe("video");
    expect(moduleForPath("/dashboard/compliance")).toBe("compliance");
    expect(moduleForPath("/dashboard/support")).toBe("support");
  });

  it("falls back to overview for unknown routes", () => {
    expect(moduleForPath("/dashboard/unknown")).toBe("overview");
  });
});
