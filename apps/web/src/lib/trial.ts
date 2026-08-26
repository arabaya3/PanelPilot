import type { components } from '@panelpilot/shared-types';

type QuotaStatus = components['schemas']['QuotaStatus'];

/**
 * The anonymous trial, and upgrading it to an account.
 *
 * The point of this flow is the deliberate contrast with an invite-only
 * funnel: a chat input above the fold, no access form in front of it, and the
 * first questions answered before anyone is asked for an email. When the limit
 * is reached, one signup step continues the same conversation.
 *
 * **Starting a trial has no endpoint yet.** The backend is ready to *finish*
 * one — `signup` takes `claim_session_id` and `claim_secret`, the
 * `anonymous_sessions` table exists, and the claim joins the new user to the
 * trial's existing tenant rather than copying rows — but nothing issues an
 * anonymous session, and every `/diagnostics` route requires
 * `CurrentUserDep`. So "the first N messages work with zero auth" cannot
 * happen over the wire today.
 *
 * What that means here: the claim half is built against the real contract and
 * works the moment a start endpoint exists. `startTrial` posts where that
 * endpoint will live and reports `unavailable` when it is absent, which is
 * what happens now — surfaced honestly rather than as a landing page that
 * silently does nothing when someone types their first question.
 *
 * The secret is the part worth getting right, and the backend is explicit
 * about why: the session id travels in URLs and is not secret, so accepting it
 * alone would let anyone who learned one join that tenant as a full user. It
 * is held only by the browser that started the trial and sent once, at claim.
 */

export const TRIAL_STORAGE_KEY = 'panelpilot.trial';

/** What the browser holds for an in-progress trial. */
export interface TrialSession {
  sessionId: string;
  /** Never logged, never put in a URL, never shown. */
  claimSecret: string;
}

export type TrialStart =
  { kind: 'started'; trial: TrialSession } | { kind: 'unavailable' } | { kind: 'failed' };

/**
 * Read the trial this browser started, if any.
 *
 * Storage is shared with everything else on the origin and survives deploys,
 * so a junk value is narrowed away rather than trusted. A trial that cannot be
 * read is simply a new trial, which is the safe direction: the alternative is
 * a signup that tries to claim a session that does not exist and fails at the
 * one moment the engineer is committing.
 */
export function readTrial(): TrialSession | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(TRIAL_STORAGE_KEY);
  } catch {
    // Private browsing, or blocked storage. Neither is a reason to fail.
    return null;
  }
  if (!raw) return null;

  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return null;
    const { sessionId, claimSecret } = parsed as Record<string, unknown>;
    if (typeof sessionId !== 'string' || sessionId === '') return null;
    if (typeof claimSecret !== 'string' || claimSecret === '') return null;
    return { sessionId, claimSecret };
  } catch {
    return null;
  }
}

/** Remember a trial across a reload, so a refresh does not lose the thread. */
export function storeTrial(trial: TrialSession): void {
  try {
    window.localStorage.setItem(TRIAL_STORAGE_KEY, JSON.stringify(trial));
  } catch {
    // A trial that does not survive a reload is worse than one that does, and
    // far better than refusing to start.
  }
}

/** Forget it, once claimed — the secret has no further use. */
export function clearTrial(): void {
  try {
    window.localStorage.removeItem(TRIAL_STORAGE_KEY);
  } catch {
    // Nothing to do; the secret is single-use at the server anyway.
  }
}

/** Has the trial run out of free questions? */
export function limitReached(quota: QuotaStatus): boolean {
  return quota.questions_remaining <= 0;
}

export interface StartOptions {
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * Begin an anonymous trial.
 *
 * Reports `unavailable` rather than throwing when the endpoint is absent,
 * because that is the current state and the landing page has to say something
 * true about it.
 */
export async function startTrial(options: StartOptions = {}): Promise<TrialStart> {
  const { fetchImpl = fetch, endpoint = '/api/v1/auth/trial' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  } catch {
    return { kind: 'failed' };
  }

  if (response.status === 404 || response.status === 405) return { kind: 'unavailable' };
  if (!response.ok) return { kind: 'failed' };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }

  const trial = readStartPayload(payload);
  return trial ? { kind: 'started', trial } : { kind: 'failed' };
}

function readStartPayload(payload: unknown): TrialSession | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const { session_id: sessionId, claim_secret: claimSecret } = payload as Record<string, unknown>;
  if (typeof sessionId !== 'string' || sessionId === '') return null;
  if (typeof claimSecret !== 'string' || claimSecret === '') return null;
  return { sessionId, claimSecret };
}

export interface SignupOptions {
  email: string;
  password: string;
  fullName?: string;
  /** The trial to carry into the new account, if this browser has one. */
  trial?: TrialSession | null;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

export type SignupOutcome =
  | { kind: 'signed-up'; accessToken: string; refreshToken: string }
  | { kind: 'email-taken' }
  | { kind: 'trial-gone' }
  | { kind: 'failed' };

/**
 * Create the account, carrying the trial conversation into it.
 *
 * The claim is sent with the signup rather than as a second call, which is
 * what makes "no approval wait" true rather than merely quick: there is one
 * step, and the conversation is already under the right tenant when it
 * returns — nothing is copied, so nothing can half-succeed.
 */
export async function signupClaimingTrial(options: SignupOptions): Promise<SignupOutcome> {
  const {
    email,
    password,
    fullName,
    trial,
    fetchImpl = fetch,
    endpoint = '/api/v1/auth/signup',
  } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email,
        password,
        ...(fullName ? { full_name: fullName } : {}),
        // Both, or neither. The id alone is not a credential.
        ...(trial ? { claim_session_id: trial.sessionId, claim_secret: trial.claimSecret } : {}),
      }),
    });
  } catch {
    return { kind: 'failed' };
  }

  if (response.status === 409) return { kind: 'email-taken' };
  // The trial expired or was already claimed. Distinguished from a generic
  // failure because the remedy differs: the account can still be created, it
  // just will not carry the conversation.
  if (response.status === 404) return { kind: 'trial-gone' };
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
  if (typeof accessToken !== 'string' || typeof refreshToken !== 'string') {
    return { kind: 'failed' };
  }
  return { kind: 'signed-up', accessToken, refreshToken };
}
