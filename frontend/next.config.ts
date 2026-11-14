import type { NextConfig } from "next";

const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  agentRules: false,
  // Compression buffers small SSE frames before EventSource can dispatch them.
  compress: false,
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` },
    ];
  },
};

export default config;
