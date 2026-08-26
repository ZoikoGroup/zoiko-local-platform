import { beforeEach, describe, expect, it } from "vitest";
import { clearToken, getToken, saveToken } from "./auth";

describe("auth token storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns null when no token has been saved", () => {
    expect(getToken()).toBeNull();
  });

  it("round-trips a saved token", () => {
    saveToken("abc123");
    expect(getToken()).toBe("abc123");
  });

  it("overwrites a previously saved token", () => {
    saveToken("first");
    saveToken("second");
    expect(getToken()).toBe("second");
  });

  it("removes the token on clear", () => {
    saveToken("abc123");
    clearToken();
    expect(getToken()).toBeNull();
  });
});
