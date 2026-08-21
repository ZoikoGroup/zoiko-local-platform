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
    // jsdom's localStorage/sessionStorage only initialize for a real
    // origin - without this, window starts at the opaque "about:blank"
    // origin. Kept alongside the Node flag below since both are real,
    // separately-confirmed causes of the same symptom.
    environmentOptions: {
      jsdom: { url: "http://localhost/" },
    },
    // Node 25 stabilized its own native, SQLite-backed `localStorage`
    // global (previously experimental) - confirmed live that it leaks
    // into/conflicts with jsdom's window.localStorage inside the test
    // environment, leaving `localStorage.clear` not a callable function
    // (every src/lib/auth.test.ts test failed with exactly that TypeError
    // until the NODE_OPTIONS fix in package.json's "test" script was
    // added). --no-experimental-webstorage disables Node's native
    // implementation so jsdom's is the only one in play. Tried an
    // execArgv-based fix here first (pool: "forks" +
    // poolOptions.forks.execArgv) - confirmed live it does NOT take
    // effect (Vitest's fork-pool workers don't appear to honor execArgv
    // for this flag), so the actual fix lives in package.json instead.
    include: ["src/**/*.test.ts"],
  },
});
