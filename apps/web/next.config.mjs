import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Fail the build on lint or type errors rather than shipping them.
  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },

  // Emit a self-contained server bundle for the Docker runner stage, so the
  // final image needs neither node_modules nor the Next CLI.
  output: 'standalone',

  // This is an npm workspace and apps/web depends on @panelpilot/shared-types,
  // so file tracing must start at the repo root or the workspace package is
  // left out of the bundle. Setting it explicitly also fixes where server.js
  // lands, which the Dockerfile's CMD depends on.
  outputFileTracingRoot: join(here, '../..'),

  /**
   * Proxy API calls through this origin.
   *
   * The browser cannot reach the API's container hostname, and the client
   * modules post to relative paths — so without this every request lands on
   * the Next server as a 404 and the landing page reports the trial endpoint
   * missing when it is running perfectly well.
   *
   * A rewrite rather than an absolute base URL in the client: same-origin
   * requests need no CORS entry, carry cookies correctly, and keep the API's
   * address out of the browser bundle, where it would be baked in at build
   * time and wrong in every environment built elsewhere.
   */
  async rewrites() {
    const target = process.env.API_PROXY_TARGET ?? 'http://localhost:8000';
    return [{ source: '/api/:path*', destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
