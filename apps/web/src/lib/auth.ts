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
  | { kind: 'failed' };

export interface RefreshOptions {
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * Ask for a new access token with the session cookie.
 *
 * What a page does on load, to find out whether this browser is signed in,
 * and after a 401. The cookie rotates on every call; the API tolerates two
 * tabs refreshing at the same moment, so nothing here needs to coordinate
 * across tabs.
 */
export async function refreshSession(options: RefreshOptions = {}): Promise<RefreshOutcome> {
  const { fetchImpl = fetch, endpoint = `${SESSION}/refresh` } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { [CSRF_HEADER]: '1' },
      credentials: 'same-origin',
    });
  } catch {
    return { kind: 'failed' };
  }

  if (response.status === 401) return { kind: 'expired' };
  if (!response.ok) {
    // A 403 is the cross-site check refusing the page's own origin: a
    // deployment whose WEB_ORIGINS does not name this site.
    if (response.status === 403) console.warn('The API refused this origin for the session.');
    return { kind: 'failed' };
  }
  const accessToken = await accessTokenOf(response);
  return accessToken === null ? { kind: 'failed' } : { kind: 'refreshed', accessToken };
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
