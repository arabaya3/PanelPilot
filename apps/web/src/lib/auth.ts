/**
 * Signing in to an existing account, and the server-side session behind it.
 *
 * The other half of `signupClaimingTrial`. The access token is held in the
 * page's memory only, never in storage. The refresh token is never seen by
 * this code at all: the API sets it as an HttpOnly cookie, scoped to
 * `/api/v1/auth/session`, so no script on the page can read it. A reload
 * signs the account back in by asking that endpoint for a new access token
 * (`refreshSession`); the browser sends the cookie on its own.
 *
 * Every request to the session endpoints carries `CSRF_HEADER`. The API
 * refuses one without it, or from another origin, so another site cannot use
 * the cookie.
 */

/** The header the API's cross-site check requires on the session endpoints. */
export const CSRF_HEADER = 'X-PanelPilot-CSRF';

const SESSION = '/api/v1/auth/session';

export type SignInOutcome =
  | { kind: 'signed-in'; accessToken: string }
  /** Wrong email or password; the server does not say which. */
  | { kind: 'invalid' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

export interface SignInOptions {
  email: string;
  password: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/** The access token in a sign-in, signup or refresh answer, or null when it has none. */
export async function accessTokenOf(response: Response): Promise<string | null> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return null;
  }
  if (typeof payload !== 'object' || payload === null) return null;
  const { access_token: accessToken } = payload as Record<string, unknown>;
  return typeof accessToken === 'string' && accessToken !== '' ? accessToken : null;
}

export async function signIn(options: SignInOptions): Promise<SignInOutcome> {
  const { email, password, fetchImpl = fetch, endpoint = '/api/v1/auth/login' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Same-origin, so the session cookie the answer sets is kept.
      credentials: 'same-origin',
      body: JSON.stringify({ email, password }),
    });
  } catch {
    return { kind: 'failed' };
  }

  // 422 is an address that is not an email at all; to the person typing it,
  // that is the same mistake as a wrong one.
  if (response.status === 401 || response.status === 422) return { kind: 'invalid' };
  if (response.status === 429) return { kind: 'rate-limited' };
  if (!response.ok) return { kind: 'failed' };

  const accessToken = await accessTokenOf(response);
  return accessToken === null ? { kind: 'failed' } : { kind: 'signed-in', accessToken };
}

export type RefreshOutcome =
  | { kind: 'refreshed'; accessToken: string }
  /** No session: never signed in in this browser, signed out, or ended elsewhere. */
  | { kind: 'expired' }
  /** No answer within the time allowed: whether a session exists is unknown. */
  | { kind: 'timed-out' }
  | { kind: 'failed' };

export interface RefreshOptions {
  fetchImpl?: typeof fetch;
  endpoint?: string;
  /** How long to wait for the answer before giving up; see `REFRESH_TIMEOUT_MS`. */
  timeoutMs?: number;
  /**
   * The access token in memory, when there is one. Sent as the bearer: proof
   * that this page is the session's own holder, which lets a cookie that an
   * out-of-order answer moved back one rotation still refresh (the API's
   * `auth.refresh`). Never required.
   */
  accessToken?: string | null;
}

/** A page waits this long, on load, to learn whether it is signed in. */
export const REFRESH_TIMEOUT_MS = 8000;

/**
 * Ask for a new access token with the session cookie.
 *
 * What a page does on load, to find out whether this browser is signed in,
 * and after a 401. The cookie rotates on every call; the API tolerates two
 * tabs refreshing at the same moment, so nothing here needs to coordinate
 * across tabs.
 */
export async function refreshSession(options: RefreshOptions = {}): Promise<RefreshOutcome> {
  const {
    fetchImpl = fetch,
    endpoint = `${SESSION}/refresh`,
    timeoutMs = REFRESH_TIMEOUT_MS,
    accessToken = null,
  } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: {
        [CSRF_HEADER]: '1',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      credentials: 'same-origin',
      // Without it a hung API left the page behind its restoring placeholder forever.
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    // By name: the abort reason is a DOMException from whichever realm made the signal.
    if (error instanceof Error && error.name === 'TimeoutError') return { kind: 'timed-out' };
    return { kind: 'failed' };
  }

  if (response.status === 401) return { kind: 'expired' };
  if (!response.ok) {
    // A 403 is the cross-site check refusing the page's own origin: a
    // deployment whose WEB_ORIGINS does not name this site.
    if (response.status === 403) console.warn('The API refused this origin for the session.');
    return { kind: 'failed' };
  }
  const renewed = await accessTokenOf(response);
  return renewed === null ? { kind: 'failed' } : { kind: 'refreshed', accessToken: renewed };
}

export type SignOutOutcome = { kind: 'signed-out' } | { kind: 'failed' };

export interface SignOutOptions {
  /** The access token in hand, if any: it names the session to end. */
  accessToken: string | null;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * End this sign-in on the server: its refresh cookie and every access token
 * it issued stop working there, at once, not just on this page. Idempotent
 * on the server. The caller clears its own token whatever this answers.
 */
export async function signOutOfServer(options: SignOutOptions): Promise<SignOutOutcome> {
  const { accessToken, fetchImpl = fetch, endpoint = `${SESSION}/logout` } = options;
  return postSession(fetchImpl, endpoint, accessToken);
}

/**
 * End every sign-in of the account, on every device. Needs a live access
 * token: the server will not end other devices' sessions on a cookie alone.
 */
export async function signOutEverywhere(options: SignOutOptions): Promise<SignOutOutcome> {
  const { accessToken, fetchImpl = fetch, endpoint = `${SESSION}/logout-everywhere` } = options;
  return postSession(fetchImpl, endpoint, accessToken);
}

async function postSession(
  fetchImpl: typeof fetch,
  endpoint: string,
  accessToken: string | null,
): Promise<SignOutOutcome> {
  try {
    const response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: {
        [CSRF_HEADER]: '1',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      credentials: 'same-origin',
    });
    return response.ok ? { kind: 'signed-out' } : { kind: 'failed' };
  } catch {
    return { kind: 'failed' };
  }
}

/** The part of the Web Locks API this needs (`navigator.locks`). */
export interface RefreshLocks {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

/** The part of `BroadcastChannel` this needs. */
export interface RefreshChannel {
  postMessage(message: unknown): void;
  addEventListener(type: 'message', listener: (event: MessageEvent) => void): void;
  removeEventListener(type: 'message', listener: (event: MessageEvent) => void): void;
}

export interface RefreshCoordination {
  locks?: RefreshLocks | undefined;
  channel?: RefreshChannel | undefined;
  now?: () => number;
}

export interface RefreshRequest {
  /** The access token in hand, sent as proof of possession; null on load. */
  accessToken: string | null;
  /**
   * Whether a token another tab shared may stand for this tab's refresh: it
   * must be unexpired and, for a signed-in tab, of the same account and
   * session. A token that fails is ignored and this tab refreshes itself.
   */
  accept: (accessToken: string) => boolean;
}

export interface RefreshCoordinator {
  refresh: (request: RefreshRequest) => Promise<RefreshOutcome>;
  /**
   * Ask the browser's other open tabs for a current access token, waiting at
   * most `waitMs`. A reloaded page has none in memory; a borrowed one, sent as
   * the bearer, lets its refresh recover a cookie that an out-of-order answer
   * moved back one rotation. Resolves null when no tab answers in time, or
   * none of the answers is accepted.
   */
  borrow: (accept: (accessToken: string) => boolean, waitMs?: number) => Promise<string | null>;
  /** Forget any shared token: on sign-out, so it cannot stand for the next account. */
  forget: () => void;
  close: () => void;
}

/** How far ahead of this tab's clock a shared timestamp may be, for skew between tabs. */
export const SHARED_CLOCK_SKEW_MS = 2000;

/** How long a reloaded page waits for another tab to lend it an access token. */
export const BORROW_WAIT_MS = 300;

const LOCK_NAME = 'pp-refresh';
const SHARED = 'pp-refreshed';
const ASK = 'pp-who-has';
const LEND = 'pp-has';

/**
 * The browser's own Web Locks and a BroadcastChannel, where both exist.
 *
 * Both or neither: without a lock there is nothing to wait for, so a shared
 * token would only ever be read too late to matter.
 */
export function browserRefreshCoordination(): RefreshCoordination {
  if (browserCoordination !== null) return browserCoordination;
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks;
  browserCoordination =
    locks === undefined || typeof BroadcastChannel === 'undefined'
      ? {}
      : { locks, channel: new BroadcastChannel(LOCK_NAME) };
  return browserCoordination;
}

// One per tab: every provider in it shares the lock and the channel.
let browserCoordination: RefreshCoordination | null = null;

/**
 * Serialise refreshes across every tab of this browser.
 *
 * Tabs share the one session cookie. Refreshing from several at once makes
 * the API rotate it once and answer the rest with the same successor (see the
 * API's reuse window); that is tolerated, but only briefly. So a refresh takes
 * a browser-wide Web Lock first, and the tab holding it shares the access
 * token it got over a BroadcastChannel. A tab that waited for the lock and
 * finds a token refreshed after it asked uses that, instead of presenting the
 * cookie again; otherwise it refreshes with the cookie as it now is. Normally,
 * then, one refresh is in flight per browser.
 *
 * Where Web Locks are missing (an old browser, the test DOM) it refreshes
 * directly, and the API's window covers the overlap.
 *
 * The token is shared only between same-origin tabs, in memory; nothing is
 * stored.
 */
export function createRefreshCoordinator(
  refresh: (accessToken: string | null) => Promise<RefreshOutcome>,
  coordination: RefreshCoordination = browserRefreshCoordination(),
  /** This tab's current access token, lent to a reloaded tab that asks; null for none. */
  lend: () => string | null = () => null,
): RefreshCoordinator {
  const { locks, channel, now = () => Date.now() } = coordination;
  let latest: { accessToken: string; at: number } | null = null;
  const borrowing = new Map<string, (accessToken: string) => void>();

  const listener = (event: MessageEvent) => {
    const data: unknown = event.data;
    if (typeof data !== 'object' || data === null) return;
    const { kind, accessToken, at, id } = data as Record<string, unknown>;
    if (kind === ASK && typeof id === 'string') {
      const mine = lend();
      if (mine !== null) channel?.postMessage({ kind: LEND, id, accessToken: mine });
      return;
    }
    if (kind === LEND && typeof id === 'string' && typeof accessToken === 'string') {
      borrowing.get(id)?.(accessToken);
      return;
    }
    if (kind !== SHARED || typeof accessToken !== 'string' || typeof at !== 'number') return;
    // A sender's clock is not trusted far into the future: a timestamp there
    // would make its token look newer than every later refresh.
    if (!Number.isFinite(at) || at > now() + SHARED_CLOCK_SKEW_MS) return;
    if (latest === null || at > latest.at) latest = { accessToken, at };
  };
  channel?.addEventListener('message', listener);

  async function refreshAndShare(accessToken: string | null): Promise<RefreshOutcome> {
    const outcome = await refresh(accessToken);
    if (outcome.kind === 'refreshed') {
      latest = { accessToken: outcome.accessToken, at: now() };
      channel?.postMessage({ kind: SHARED, ...latest });
    }
    return outcome;
  }

  return {
    refresh: ({ accessToken, accept }) => {
      if (locks === undefined) return refresh(accessToken);
      const asked = now();
      return locks.request(LOCK_NAME, async () => {
        // Another tab refreshed while this one waited: its token stands for
        // this one's, if it is for the same account and session and still
        // valid; the cookie has already moved on.
        const shared = latest;
        if (shared !== null && shared.at >= asked && accept(shared.accessToken)) {
          return { kind: 'refreshed', accessToken: shared.accessToken } as const;
        }
        return refreshAndShare(accessToken);
      });
    },
    borrow: (accept, waitMs = BORROW_WAIT_MS) => {
      if (channel === undefined) return Promise.resolve(null);
      const id = `${String(now())}-${Math.random().toString(36).slice(2)}`;
      return new Promise<string | null>((resolve) => {
        const done = (token: string | null) => {
          borrowing.delete(id);
          clearTimeout(timer);
          resolve(token);
        };
        // The first answer that passes the same checks as a shared token.
        borrowing.set(id, (token) => {
          if (accept(token)) done(token);
        });
        const timer = setTimeout(() => {
          done(null);
        }, waitMs);
        channel.postMessage({ kind: ASK, id });
      });
    },
    forget: () => {
      latest = null;
    },
    // The channel is the tab's, shared; only this coordinator's ear is removed.
    close: () => {
      channel?.removeEventListener('message', listener);
    },
  };
}
