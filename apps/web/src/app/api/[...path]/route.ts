/**
 * Proxy `/api/v1/*` from this origin to the API.
 *
 * The browser cannot reach the API's container hostname, and the client
 * modules post to relative paths, so something on this origin has to forward
 * them. Same-origin requests also need no CORS entry and keep the API's
 * address out of the browser bundle.
 *
 * **A route handler, not a `rewrites()` entry.** A rewrite's destination is
 * evaluated when `next build` runs and baked into `routes-manifest.json`, so
 * the production image proxied to whatever `API_PROXY_TARGET` was in the
 * *builder* stage — which set none, leaving every deployed container sending
 * API traffic to `localhost:8000` inside itself. Reading the variable here, per
 * request, makes it a runtime setting like every other one.
 *
 * Streaming both ways, never buffered: the diagnostic stream is SSE and
 * useless if it arrives all at once at the end, and photo uploads are several
 * megabytes that there is no reason to hold in this process's memory.
 */

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

/**
 * Where requests go when nothing says otherwise: the API as `next dev` sees
 * it on a developer's machine outside compose.
 */
const DEFAULT_TARGET = 'http://localhost:8000';

/** Only the versioned API is forwarded, so this is not an open proxy. */
const PROXIED_PREFIX = '/api/v1/';

/**
 * Request headers passed upstream. An allowlist rather than a denylist: the
 * API needs these and nothing else, and forwarding the browser's cookies or
 * `host` to a service that never asked for them is how a proxy leaks.
 */
const FORWARDED_REQUEST_HEADERS = ['authorization', 'content-type', 'accept', 'x-correlation-id'];

/**
 * Response headers that describe this hop rather than the body, and must not
 * be copied onto a different connection. `content-encoding` and
 * `content-length` join them because `fetch` has already decoded the body, so
 * both would describe bytes that are no longer the ones being sent.
 */
const DROPPED_RESPONSE_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'content-encoding',
  'content-length',
]);

/** `RequestInit` as Node's `fetch` accepts it; the DOM lib has no `duplex`. */
type StreamingInit = RequestInit & { duplex?: 'half' };

/**
 * The client address chain to hand the API.
 *
 * The API rate-limits per client IP, and without this header every request
 * would appear to come from this server, putting every visitor in one bucket.
 *
 * Next's server sets `x-forwarded-for` to the socket's peer address when the
 * request arrived without one, and leaves an existing chain untouched — a
 * route handler has no other way to see the peer. So the chain is forwarded as
 * Next presents it. A deployment with a load balancer in front must have that
 * balancer overwrite the header, since whatever the first hop accepts from the
 * internet is what arrives here.
 */
function clientChain(request: Request): string | null {
  const chain = request.headers.get('x-forwarded-for');
  if (chain !== null && chain.trim() !== '') return chain;
  const real = request.headers.get('x-real-ip');
  return real !== null && real.trim() !== '' ? real : null;
}

async function proxy(request: Request): Promise<Response> {
  const incoming = new URL(request.url);

  if (!incoming.pathname.startsWith(PROXIED_PREFIX)) {
    return Response.json({ detail: 'Not Found' }, { status: 404 });
  }

  // Read per request, deliberately — see the module comment.
  const target = (process.env.API_PROXY_TARGET ?? DEFAULT_TARGET).replace(/\/+$/, '');
  const upstreamUrl = `${target}${incoming.pathname}${incoming.search}`;

  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const chain = clientChain(request);
  if (chain !== null) headers.set('x-forwarded-for', chain);

  const hasBody = request.method !== 'GET' && request.method !== 'HEAD' && request.body !== null;
  const init: StreamingInit = {
    method: request.method,
    headers,
    // Manual, so a redirect from the API reaches the browser rather than
    // being followed here with the caller's bearer token attached.
    redirect: 'manual',
    // Aborted when the browser goes away, so a closed tab does not leave an
    // upstream stream running. The diagnostic stream relies on the
    // disconnect to avoid billing for an answer nobody received.
    signal: request.signal,
    ...(hasBody ? { body: request.body, duplex: 'half' } : {}),
  };

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, init);
  } catch {
    // The API is down or unreachable. A JSON 502 keeps the client modules on
    // their `failed` paths instead of parsing Next's HTML error page.
    return Response.json({ detail: 'Upstream API unavailable' }, { status: 502 });
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, name) => {
    if (!DROPPED_RESPONSE_HEADERS.has(name.toLowerCase())) responseHeaders.set(name, value);
  });
  if (responseHeaders.get('content-type')?.startsWith('text/event-stream')) {
    // `no-transform` is what stops Next's gzip middleware from buffering the
    // event stream until it closes, which would deliver every progress stage
    // at once — the exact thing the stages exist to avoid.
    responseHeaders.set('cache-control', 'no-cache, no-transform');
  }

  // The body stream is passed straight through: nothing here reads it.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
