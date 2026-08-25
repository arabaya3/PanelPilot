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
};

export default nextConfig;
