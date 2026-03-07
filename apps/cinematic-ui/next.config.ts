import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    optimizePackageImports: [],
    devtoolSegmentExplorer: false
  }
};

export default nextConfig;
