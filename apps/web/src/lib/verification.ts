/**
 * The reviewer's side of the API: signing in, loading the queue, labelling.
 *
 * Same shape as `sessions.ts` — outcomes as discriminated unions, payloads
 * narrowed rather than cast. The access token is held in memory by the page
 * and never stored: a reviewer's token can publish to production, and leaving
 * one in storage on a shared machine is a disclosure nobody asked for.
 */
import type { components } from '@panelpilot/shared-types';

import { ClaimConflict, type VerificationApi } from '@/components/verification';

export type QueueItem = components['schemas']['QueueItem'];
type VerificationLabel = components['schemas']['VerificationLabel'];

export type SignInOutcome =
  { kind: 'signed-in'; token: string } | { kind: 'rejected' } | { kind: 'failed' };

export type QueueOutcome =
  | { kind: 'loaded'; items: QueueItem[] }
  /** Signed in, but the account cannot review. */
  | { kind: 'forbidden' }
  | { kind: 'failed' };

/** Exchange credentials for an access token. */
export async function signIn(
  email: string,
  password: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SignInOutcome> {
  let response: Response;
  try {
    response = await fetchImpl('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401 || response.status === 422) return { kind: 'rejected' };
  if (!response.ok) return { kind: 'failed' };
  const body: unknown = await response.json().catch(() => null);
  const token =
    typeof body === 'object' && body !== null
      ? (body as { access_token?: unknown }).access_token
      : undefined;
  return typeof token === 'string' && token !== ''
    ? { kind: 'signed-in', token }
    : { kind: 'failed' };
}

/**
 * Whether a token grants the reviewer role.
 *
 * Read from the token's own claims so the page can say "this account cannot
 * review" before offering a queue whose `correct` labels would all be refused.
 * The server re-checks on every request; this only decides what to show.
 */
export function tokenIsReviewer(token: string): boolean {
  const payload = token.split('.')[1];
  if (payload === undefined) return false;
  try {
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const roles = (JSON.parse(json) as { roles?: unknown }).roles;
    return Array.isArray(roles) && roles.includes('reviewer');
  } catch {
    return false;
  }
}

/** Load the signed-in reviewer's outstanding items. */
export async function loadQueue(
  token: string,
  fetchImpl: typeof fetch = fetch,
): Promise<QueueOutcome> {
  let response: Response;
  try {
    response = await fetchImpl('/api/v1/verification/queue/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 403) return { kind: 'forbidden' };
  if (!response.ok) return { kind: 'failed' };
  const body: unknown = await response.json().catch(() => null);
  const items =
    typeof body === 'object' && body !== null ? (body as { items?: unknown }).items : undefined;
  if (!Array.isArray(items)) return { kind: 'failed' };
  return {
    kind: 'loaded',
    items: items.filter(
      (item): item is QueueItem =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as { id?: unknown }).id === 'string' &&
        typeof (item as { status?: unknown }).status === 'string',
    ),
  };
}

/**
 * The console's API, bound to a token.
 *
 * A 403 means the item is not this reviewer's — someone else holds it — which
 * the console handles by dropping it and saying so. Every other failure,
 * including a promotion refusal (409), is surfaced as a failed submit.
 */
export function verificationApi(token: string, fetchImpl: typeof fetch = fetch): VerificationApi {
  return {
    async submitLabel(itemId: string, label: VerificationLabel, note: string): Promise<void> {
      const response = await fetchImpl(
        `/api/v1/verification/items/${encodeURIComponent(itemId)}/label`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ label, note }),
        },
      );
      if (response.status === 403) throw new ClaimConflict('another reviewer');
      if (!response.ok) throw new Error(`label rejected with ${String(response.status)}`);
    },
  };
}
