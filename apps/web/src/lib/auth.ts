/**
 * Signing in to an existing account.
 *
 * The other half of `signupClaimingTrial`. Without it an account existed
 * only for as long as the tab that created it: tokens are held in memory by
 * design, so a reload or a new day left no way back in except another trial,
 * and the signup error "That email already has an account. Sign in instead."
 * pointed at a form that did not exist.
 */

export type SignInOutcome =
  | { kind: 'signed-in'; accessToken: string; refreshToken: string }
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

export async function signIn(options: SignInOptions): Promise<SignInOutcome> {
  const { email, password, fetchImpl = fetch, endpoint = '/api/v1/auth/login' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }
  if (typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  const { access_token: accessToken, refresh_token: refreshToken } = payload as Record<
    string,
    unknown
  >;
  if (typeof accessToken !== 'string' || accessToken === '' || typeof refreshToken !== 'string') {
    return { kind: 'failed' };
  }
  return { kind: 'signed-in', accessToken, refreshToken };
}
