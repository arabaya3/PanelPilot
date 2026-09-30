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

type StaleDocument = components['schemas']['StaleDocument'];

export type StaleOutcome =
  | { kind: 'loaded'; items: StaleDocument[] }
  | { kind: 'forbidden' }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/**
 * Live documents whose source now serves something other than what was
 * verified, as flagged by the worker's `expire-stale-sources`.
 */
export async function fetchStale(options: {
  token: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<StaleOutcome> {
  const {
    token,
    fetchImpl = fetch,
    endpoint = '/api/v1/verification/stale-documents?status=open',
  } = options;

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
  return { kind: 'loaded', items: items as StaleDocument[] };
}

/**
 * Take a flagged document's passages out of live answers, and say why.
 *
 * @throws Error when the retraction is refused or fails, carrying the
 *   server's reason: nothing was removed and nothing recorded.
 */
export async function retractStale(options: {
  token: string;
  id: string;
  note: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<void> {
  await postStaleDecision({ ...options, action: 'retract' });
}

/**
 * Record that an upstream change is harmless, and why.
 *
 * @throws Error when the dismissal is refused, carrying the server's reason
 *   when it gave one: a blank note, or a flag someone else already decided.
 */
export async function dismissStale(options: {
  token: string;
  id: string;
  note: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<void> {
  await postStaleDecision({ ...options, action: 'dismiss' });
}

async function postStaleDecision(options: {
  token: string;
  id: string;
  note: string;
  action: 'dismiss' | 'retract';
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<void> {
  const {
    token,
    id,
    note,
    action,
    fetchImpl = fetch,
    endpoint = '/api/v1/verification/stale-documents',
  } = options;

  const response = await fetchImpl(`${endpoint}/${encodeURIComponent(id)}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ note }),
  });
  if (response.ok) return;
  let detail = '';
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') detail = body.detail;
  } catch {
    // No body worth reading; the status says enough.
  }
  throw new Error(detail || `${action} refused: ${String(response.status)}`);
}
