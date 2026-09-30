import type { components } from '@panelpilot/shared-types';

export type CableSizingRequest = components['schemas']['CableSizingRequest'];
export type CableSizingResponse = components['schemas']['CableSizingResponse'];

export type CableSizingOutcome =
  | { kind: 'sized'; response: CableSizingResponse }
  /** Outside what the tables cover; `detail` says which input and why. */
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/**
 * Size a feeder cable: `POST /api/v1/calculations/cable-sizing`.
 *
 * A 422 is the calculation refusing an input its tables do not cover -- an
 * answer to show the engineer, not a fault.
 */
export async function sizeCable(options: {
  token: string;
  request: CableSizingRequest;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<CableSizingOutcome> {
  const {
    token,
    request,
    fetchImpl = fetch,
    endpoint = '/api/v1/calculations/cable-sizing',
  } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(request),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 422) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    return { kind: 'refused', detail: typeof detail === 'string' ? detail : '' };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('result' in payload) || !('sources' in payload)) return { kind: 'failed' };
  return { kind: 'sized', response: payload as CableSizingResponse };
}
