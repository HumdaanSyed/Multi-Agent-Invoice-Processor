import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server.js + only the traced node_modules it needs, so the
  // runtime Docker image doesn't ship the full dependency tree (web/Dockerfile).
  output: "standalone",
  // gzip buffers the SSE stream coming through app/api/[...path]; nginx
  // (deploy/nginx/verity.conf) compresses at the edge instead.
  compress: false,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          // SAMEORIGIN, not DENY: the run page previews the PDF in an iframe from /api.
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "Content-Security-Policy", value: "frame-ancestors 'self'; base-uri 'self'; object-src 'none'" },
        ],
      },
    ];
  },
};

export default nextConfig;
