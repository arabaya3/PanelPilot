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

  // Nothing gains from announcing the framework and its version to a scanner.
  poweredByHeader: false,

  // API calls are proxied by the route handler in src/app/api/[...path], not a
  // `rewrites()` entry here. A rewrite's destination is resolved at build time
  // and baked into routes-manifest.json, so the production image forwarded to
  // whatever API_PROXY_TARGET the builder stage had — none — and every
  // deployed container sent API traffic to localhost:8000 inside itself. The
  // handler reads the variable per request instead.

  /**
   * Security headers on every response.
   *
   * The Content-Security-Policy is deliberately narrow. `frame-ancestors`,
   * `object-src`, `base-uri` and `form-action` restrict nothing Next itself
   * needs, whereas a `script-src` would block the inline bootstrap scripts
   * Next emits unless every page carried a nonce — a policy that breaks the
   * app is one somebody deletes. Clickjacking is covered twice, by
   * `frame-ancestors` and by X-Frame-Options for browsers that predate it.
   *
   * The camera stays allowed for this origin: photographing a drive's display
   * is a core feature, and `capture="environment"` needs it.
   */
  async headers() {
    const headers = [
      { key: 'X-Frame-Options', value: 'DENY' },
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
      { key: 'Permissions-Policy', value: 'camera=(self), microphone=(), geolocation=()' },
      {
        key: 'Content-Security-Policy',
        value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
      },
    ];
    // Production only. HSTS is remembered by the browser for a year, and
    // sending it from `next dev` on localhost would pin every other local
    // service on that host to HTTPS too.
    if (process.env.NODE_ENV === 'production') {
      headers.push({
        key: 'Strict-Transport-Security',
        value: 'max-age=31536000; includeSubDomains',
      });
    }
    return [{ source: '/:path*', headers }];
  },
};

export default nextConfig;
