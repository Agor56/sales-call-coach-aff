import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // .next/standalone = server.js + only the node_modules files it uses, so the full node_modules can be deleted after a build
  output: "standalone",
  // the API routes read data/ at runtime (process.cwd()), so the tracer would copy it in — the live copy is read via COACH_DATA_DIR
  outputFileTracingExcludes: { "/*": ["./data/**/*", "./src/**/*"] },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    root: __dirname,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
