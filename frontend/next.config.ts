import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Produces a self-contained .next/standalone output (server + only the
  // node_modules it actually needs) - lets the Dockerfile copy just that
  // instead of the full node_modules tree into the runtime image.
  output: "standalone",
};

export default nextConfig;
