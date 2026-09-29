import type { components } from '@panelpilot/shared-types';

import { ClaimConflict } from '@/components/verification';

type QueueItem = components['schemas']['QueueItem'];
type VerificationLabel = components['schemas']['VerificationLabel'];

/**
 * A reviewer's queue: read it, and label what is in it.
 *
 * A label is a publication decision. Labelling an item `correct` promotes its
 * chunk to the production index in the same request (ADR 0001), so a failure
 * here is reported, never retried behind the reviewer's back.
 */

export type QueueOutcome =
  | { kind: 'loaded'; items: QueueItem[] }
  /** Signed in, but without the reviewer role. */
  | { kind: 'forbidden' }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

export async function fetchQueue(options: {
  token: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<QueueOutcome> {
  const { token, fetchImpl = fetch, endpoint = '/api/v1/verification/queue/me' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  if (response.status === 403) return { kind: 'forbidden' };
  if (!response.ok) return { kind: 'failed' };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }
  if (typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  const { items } = payload as { items?: unknown };
  if (!Array.isArray(items)) return { kind: 'failed' };
  return { kind: 'loaded', items: items as QueueItem[] };
}

/**
 * Record a label.
 *
 * @throws ClaimConflict when the item is no longer this reviewer's (403): the
 *   console removes it and says why. Any other failure throws a plain error,
 *   which the console reports without removing the item.
 */
export async function submitLabel(options: {
  token: string;
  itemId: string;
  label: VerificationLabel;
  note: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<void> {
  const {
    token,
    itemId,
    label,
    note,
    fetchImpl = fetch,
    endpoint = '/api/v1/verification/items',
  } = options;

  const response = await fetchImpl(`${endpoint}/${encodeURIComponent(itemId)}/label`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ label, note }),
  });
  if (response.status === 403) throw new ClaimConflict('another reviewer');
  if (!response.ok) throw new Error(`label refused: ${String(response.status)}`);
}

/**
 * Where to show an item's source: its document, at its page when known.
 *
 * `#page=` is the PDF open-parameters fragment; a viewer that does not
 * understand it ignores it and shows the document from the start.
 */
export function sourceUrlFor(item: QueueItem): string | null {
  if (!item.source_url) return null;
  return typeof item.page === 'number'
    ? `${item.source_url}#page=${String(item.page)}`
    : item.source_url;
}
