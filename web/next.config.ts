import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server.js + only the traced node_modules it needs, so the
  // runtime Docker image doesn't ship the full dependency tree (web/Dockerfile).
  output: "standalone",
};

export default nextConfig;
