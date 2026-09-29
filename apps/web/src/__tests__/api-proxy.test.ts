import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { NextConfig } from 'next';

import { GET, POST } from '@/app/api/[...path]/route';

/**
 * Tests for the same-origin API proxy and the headers every response carries.
 *
 * The proxy replaced a `rewrites()` entry whose target was fixed when the
 * image was built, so the deployed container forwarded to `localhost:8000`
 * inside itself. The first test is the regression: the target is whatever the
 * environment says at the moment the request arrives.
 *
 * These run under the suite's jsdom environment, which leaves Node's own
 * `Request`, `Response` and streams in place — the ones a route handler gets.
 */

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function upstreamUrl(): string {
  const input = fetchMock.mock.calls[0]?.[0];
  if (input === undefined) return '';
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.href : input.url;
}

function upstreamInit(): RequestInit & { duplex?: string } {
  return fetchMock.mock.calls[0]?.[1] ?? {};
}

describe('the API proxy', () => {
  it('reads the target when the request arrives, not when the app was built', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));

    vi.stubEnv('API_PROXY_TARGET', 'http://api:8000');
    await GET(new Request('http://web.test/api/v1/auth/quota'));
    expect(upstreamUrl()).toBe('http://api:8000/api/v1/auth/quota');

    fetchMock.mockClear();
    vi.stubEnv('API_PROXY_TARGET', 'http://elsewhere:9000/');
    await GET(new Request('http://web.test/api/v1/auth/quota'));
    expect(upstreamUrl()).toBe('http://elsewhere:9000/api/v1/auth/quota');
  });

  it('forwards the path, the query and the method', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
    vi.stubEnv('API_PROXY_TARGET', 'http://api:8000');

    await GET(new Request('http://web.test/api/v1/sessions?cursor=abc&limit=20'));

    expect(upstreamUrl()).toBe('http://api:8000/api/v1/sessions?cursor=abc&limit=20');
    expect(upstreamInit().method).toBe('GET');
  });

  it('streams a request body through and forwards only the headers the API needs', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));

    await POST(
      new Request('http://web.test/api/v1/diagnostics/stream', {
        method: 'POST',
        body: '{"symptom":"x"}',
        headers: {
          authorization: 'Bearer tok',
          'content-type': 'application/json',
          accept: 'text/event-stream',
          'x-correlation-id': 'corr-1',
          cookie: 'session=private',
          host: 'web.test',
          connection: 'keep-alive',
        },
      }),
    );

    const init = upstreamInit();
    const headers = new Headers(init.headers);
    expect(init.method).toBe('POST');
    expect(init.duplex).toBe('half');
    expect(init.body).toBeInstanceOf(ReadableStream);
    expect(headers.get('authorization')).toBe('Bearer tok');
    expect(headers.get('content-type')).toBe('application/json');
    expect(headers.get('accept')).toBe('text/event-stream');
    expect(headers.get('x-correlation-id')).toBe('corr-1');
    expect(headers.get('cookie')).toBeNull();
    expect(headers.get('host')).toBeNull();
    expect(headers.get('connection')).toBeNull();
  });

  it('hands the API the client address so rate limiting is per visitor', async () => {
    // Without it every request appears to come from the web container, and
    // one busy visitor rate-limits everybody.
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));

    await GET(
      new Request('http://web.test/api/v1/auth/quota', {
        headers: { 'x-forwarded-for': '203.0.113.7, 10.0.0.2' },
      }),
    );
    expect(new Headers(upstreamInit().headers).get('x-forwarded-for')).toBe(
      '203.0.113.7, 10.0.0.2',
    );

    fetchMock.mockClear();
    await GET(
      new Request('http://web.test/api/v1/auth/quota', {
        headers: { 'x-real-ip': '198.51.100.4' },
      }),
    );
    expect(new Headers(upstreamInit().headers).get('x-forwarded-for')).toBe('198.51.100.4');
  });

  it.each(['/api/v2/anything', '/api/internal', '/api/v1'])(
    'refuses %s rather than being an open proxy',
    async (path) => {
      const response = await GET(new Request(`http://web.test${path}`));
      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it('does not let a dot-segment climb out of /api/v1', async () => {
    const response = await GET(new Request('http://web.test/api/v1/%2e%2e/%2e%2e/admin'));
    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('answers 502 with JSON when the API cannot be reached', async () => {
    fetchMock.mockRejectedValue(new TypeError('fetch failed'));

    const response = await GET(new Request('http://web.test/api/v1/auth/quota'));

    expect(response.status).toBe(502);
    expect(response.headers.get('content-type')).toContain('application/json');
    expect(await response.json()).toHaveProperty('detail');
  });

  it('streams the response back as it arrives rather than buffering it', async () => {
    // The diagnostic stream's progress stages are worthless delivered all at
    // once at the end. The first frame must be readable while the upstream
    // body is still open.
    let push: ((chunk: string) => void) | undefined;
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => {
          controller.enqueue(encoder.encode(chunk));
        };
      },
    });
    fetchMock.mockResolvedValue(
      new Response(body, {
        status: 200,
        headers: {
          'content-type': 'text/event-stream',
          'cache-control': 'no-cache',
          'transfer-encoding': 'chunked',
          'x-correlation-id': 'corr-2',
        },
      }),
    );

    const response = await POST(
      new Request('http://web.test/api/v1/diagnostics/stream', { method: 'POST', body: '{}' }),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get('x-correlation-id')).toBe('corr-2');
    expect(response.headers.get('transfer-encoding')).toBeNull();
    // Keeps Next's compression from holding the stream until it closes.
    expect(response.headers.get('cache-control')).toContain('no-transform');

    const reader = (response.body as ReadableStream<Uint8Array>).getReader();
    push?.('event: retrieving\ndata: {}\n\n');
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toBe('event: retrieving\ndata: {}\n\n');
    await reader.cancel();
  });

  it('passes an upstream error status through unchanged', async () => {
    fetchMock.mockResolvedValue(
      new Response('{"detail":"nope"}', {
        status: 401,
        headers: { 'content-type': 'application/json' },
      }),
    );

    const response = await GET(new Request('http://web.test/api/v1/auth/quota'));
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: 'nope' });
  });
});

// --- security headers ----------------------------------------------------------

async function loadConfig(): Promise<NextConfig> {
  // A computed specifier, because tsconfig does not admit JavaScript and a
  // literal import of the .mjs would be an untyped-module error.
  const specifier = '@/../next.config.mjs';
  const loaded = (await import(/* @vite-ignore */ specifier)) as { default: NextConfig };
  return loaded.default;
}

async function headersFor(): Promise<Record<string, string>> {
  const config = await loadConfig();
  const rules = (await config.headers?.()) ?? [];
  const all = rules.find((rule) => rule.source === '/:path*');
  return Object.fromEntries((all?.headers ?? []).map(({ key, value }) => [key, value]));
}

describe('security headers', () => {
  it('does not advertise the framework', async () => {
    expect((await loadConfig()).poweredByHeader).toBe(false);
  });

  it('no longer proxies through a build-time rewrite', async () => {
    expect((await loadConfig()).rewrites).toBeUndefined();
  });

  it('sends the framing, sniffing, referrer and permissions headers on every route', async () => {
    const headers = await headersFor();
    expect(headers['X-Frame-Options']).toBe('DENY');
    expect(headers['X-Content-Type-Options']).toBe('nosniff');
    expect(headers['Referrer-Policy']).toBe('strict-origin-when-cross-origin');
    expect(headers['Permissions-Policy']).toBe('camera=(self), microphone=(), geolocation=()');
  });

  it('sends a CSP that cannot break Next’s inline scripts', async () => {
    const csp = (await headersFor())['Content-Security-Policy'] ?? '';
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).not.toContain('script-src');
    expect(csp).not.toContain('default-src');
  });

  it('sends HSTS in production only', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    expect(await headersFor()).not.toHaveProperty('Strict-Transport-Security');

    vi.stubEnv('NODE_ENV', 'production');
    expect((await headersFor())['Strict-Transport-Security']).toBe(
      'max-age=31536000; includeSubDomains',
    );
  });
});
