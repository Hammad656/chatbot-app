import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    const api = (
      process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000"
    ).replace(/\/$/, "");
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      { source: "/docs", destination: `${api}/api/v1/docs` },
    ];
  },
  /* config options here */
  experimental: {
    agentFeedback: true,
    proxyClientMaxBodySize: "22mb",
    proxyTimeout: 90000,
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
