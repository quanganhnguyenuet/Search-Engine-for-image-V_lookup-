import type { NextConfig } from "next";

// The browser reaches the API through this server, so one public address (a tunnel) is enough.
// Read when the app is built: rebuild after changing it.
const apiProxyUrl = (process.env.API_PROXY_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  experimental: {
    // With a proxy.ts, Next buffers request bodies and silently cuts them at 10 MB, also on
    // rewritten paths its matcher skips. The API accepts 10 MiB images plus multipart framing.
    proxyClientMaxBodySize: "12mb",
  },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiProxyUrl}/api/v1/:path*` }];
  },
};

export default nextConfig;
