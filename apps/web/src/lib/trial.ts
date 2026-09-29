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
 * `startTrial` issues an anonymous session and a token to ask with;
 * `resumeTrial` exchanges the stored claim pair for a fresh token on the same
 * trial after a reload; `signupClaimingTrial` joins the new user to the
 * trial's existing tenant rather than copying rows. `startTrial` still
 * reports `unavailable` when its endpoint is absent, so a deployment without
 * it says so rather than rendering a landing page that silently does nothing
 * when someone types their first question.
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

/** A trial and the credential to use it with, from a start or a resume. */
export interface ActiveTrial {
  trial: TrialSession;
  /**
   * The access token the trial may ask questions with.
   *
   * Carried in the start response rather than fetched separately: every
   * diagnostics route authenticates, so a trial without one is a landing
   * page that collects a question and does nothing with it.
   *
   * Deliberately NOT persisted alongside the trial. The claim pair has to
   * survive a reload; a bearer token does not, and leaving one in storage
   * on a shared workshop terminal is a disclosure nobody asked for.
   */
  accessToken: string;
  /** Free questions left on this trial, as the server counts them. */
  questionsRemaining: number;
  /**
   * The conversation the trial opened, to ask the first question in. `null`
   * from a server that does not send it, which falls back to the first
   * question opening its own.
   */
  conversationId: string | null;
}

export type TrialStart =
  ({ kind: 'started' } & ActiveTrial) | { kind: 'unavailable' } | { kind: 'failed' };

export type TrialResume =
  | ({ kind: 'resumed' } & ActiveTrial)
  /**
   * The server no longer knows this trial — expired, claimed, or never
   * issued. Distinct from `failed` because the remedy is to forget it and
   * start a new one, whereas a network failure must not throw away a
   * conversation that is still reachable.
   */
  | { kind: 'gone' }
  | { kind: 'failed' };

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

  const started = readActiveTrial(payload);
  return started ? { kind: 'started', ...started } : { kind: 'failed' };
}

export interface ResumeOptions {
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * Get a fresh token for the trial this browser already holds.
 *
 * What a reload needs. Starting a new trial instead would issue a token for a
 * *different* tenant, stranding the conversation under one the visitor can
 * no longer reach and granting a fresh quota for nothing; pairing the old
 * claim pair with that new token would be worse, since the signup would then
 * claim a tenant other than the one the questions were asked in.
 *
 * The secret goes in the body, never the URL, for the reason the module
 * comment gives.
 */
export async function resumeTrial(
  trial: TrialSession,
  options: ResumeOptions = {},
): Promise<TrialResume> {
  const { fetchImpl = fetch, endpoint = '/api/v1/auth/trial/resume' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: trial.sessionId, claim_secret: trial.claimSecret }),
    });
  } catch {
    return { kind: 'failed' };
  }

  // 401/404/422 are the server's ways of saying the trial is unknown, expired
  // or already claimed. 405 is an API without the resume route at all, which
  // leaves the same remedy: a new trial rather than no trial.
  if ([401, 404, 405, 422].includes(response.status)) return { kind: 'gone' };
  if (!response.ok) return { kind: 'failed' };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }

  // The returned pair is used as-is, alongside the token it came with, rather
  // than the one that was sent: the token and the stored trial must always
  // name the same tenant.
  const resumed = readActiveTrial(payload);
  return resumed ? { kind: 'resumed', ...resumed } : { kind: 'failed' };
}

export type QuotaOutcome =
  { kind: 'loaded'; quota: QuotaStatus } | { kind: 'unauthorized' } | { kind: 'failed' };

export interface QuotaOptions {
  token: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * Ask the server how many free questions are left.
 *
 * The server's count is the only one that means anything — the charge happens
 * after the answer is delivered, and a refused turn is not charged — so the
 * client asks after each turn rather than decrementing a number of its own.
 */
export async function fetchQuota(options: QuotaOptions): Promise<QuotaOutcome> {
  const { token, fetchImpl = fetch, endpoint = '/api/v1/auth/quota' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    return { kind: 'failed' };
  }

  if (response.status === 401) return { kind: 'unauthorized' };
  if (!response.ok) return { kind: 'failed' };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }
  if (typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  const {
    questions_used: used,
    question_limit: limit,
    questions_remaining: remaining,
  } = payload as Record<string, unknown>;
  if (typeof used !== 'number' || typeof limit !== 'number' || typeof remaining !== 'number') {
    return { kind: 'failed' };
  }
  return {
    kind: 'loaded',
    quota: { questions_used: used, question_limit: limit, questions_remaining: remaining },
  };
}

function readActiveTrial(payload: unknown): ActiveTrial | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const {
    session_id: sessionId,
    claim_secret: claimSecret,
    access_token: accessToken,
    questions_remaining: questionsRemaining,
    conversation_id: conversationId,
  } = payload as Record<string, unknown>;
  if (typeof sessionId !== 'string' || sessionId === '') return null;
  if (typeof claimSecret !== 'string' || claimSecret === '') return null;
  // Narrowed rather than defaulted: a start without a usable token is a
  // failure, and treating it as success would render an input that 401s on
  // the first question.
  if (typeof accessToken !== 'string' || accessToken === '') return null;
  return {
    trial: { sessionId, claimSecret },
    accessToken,
    questionsRemaining: typeof questionsRemaining === 'number' ? questionsRemaining : 0,
    conversationId:
      typeof conversationId === 'string' && conversationId !== '' ? conversationId : null,
  };
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

export type RefreshOutcome =
  | { kind: 'refreshed'; accessToken: string; refreshToken: string }
  | { kind: 'expired' }
  | { kind: 'failed' };

export interface RefreshOptions {
  refreshToken: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * Exchange a refresh token for a new pair, once an account's access token
 * has lapsed.
 *
 * The caller holds the refresh token in memory only, for the same reason the
 * access token is never persisted: a credential left in storage on a shared
 * workshop terminal outlives the person who signed in.
 */
export async function refreshTokens(options: RefreshOptions): Promise<RefreshOutcome> {
  const { refreshToken, fetchImpl = fetch, endpoint = '/api/v1/auth/refresh' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch {
    return { kind: 'failed' };
  }

  if (response.status === 401 || response.status === 422) return { kind: 'expired' };
  if (!response.ok) return { kind: 'failed' };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }
  if (typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  const { access_token: accessToken, refresh_token: newRefresh } = payload as Record<
    string,
    unknown
  >;
  if (typeof accessToken !== 'string' || accessToken === '' || typeof newRefresh !== 'string') {
    return { kind: 'failed' };
  }
  return { kind: 'refreshed', accessToken, refreshToken: newRefresh };
}
