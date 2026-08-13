import path from "path";
import { defineConfig } from "vitest/config";

// Minimal Vitest setup - unit tests for pure logic in src/lib, not full
// component rendering (no @testing-library/react yet - add it when a test
// actually needs to render a component, rather than pulling it in unused).
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
