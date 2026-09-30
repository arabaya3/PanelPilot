import type { components } from '@panelpilot/shared-types';

type RetrievedPassage = components['schemas']['RetrievedPassage'];

export type FlagOutcome =
  | { kind: 'sent' }
  | { kind: 'unauthorized' }
  /** The turn is gone, or not this caller's to report. */
  | { kind: 'not-found' }
  | { kind: 'failed' };

/** The longest reason the API accepts; the form stops there rather than at a 422. */
export const MAX_REASON_LENGTH = 2000;

/**
 * Report an answer as wrong: `POST /api/v1/feedback/flag`.
 *
 * The passages go with it -- the ones the answer was built on, as they were.
 * A reviewer shown whatever retrieval returns later would be judging an answer
 * nobody gave.
 */
export async function flagAnswer(options: {
  token: string;
  turnId: string;
  reason?: string;
  evidence?: RetrievedPassage[];
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<FlagOutcome> {
  const {
    token,
    turnId,
    reason = '',
    evidence = [],
    fetchImpl = fetch,
    endpoint = '/api/v1/feedback/flag',
  } = options;
  const trimmed = reason.trim();

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        message_id: turnId,
        reason: trimmed === '' ? null : trimmed,
        retrieved: evidence,
      }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  if (response.status === 404) return { kind: 'not-found' };
  if (!response.ok) return { kind: 'failed' };
  return { kind: 'sent' };
}
