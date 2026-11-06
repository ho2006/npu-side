import type { NextConfig } from "next";

const config: NextConfig = {
  async redirects() {
    return [{ source: "/", destination: "/experiments", permanent: false }];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.ECONLAB_API_URL ?? "http://127.0.0.1:8000"}/:path*`,
      },
    ];
  },
};

export default config;
