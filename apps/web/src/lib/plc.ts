import type { components } from '@panelpilot/shared-types';

type PlcValidationResult = components['schemas']['PlcValidationResult'];

/**
 * Review Structured Text an engineer already has.
 *
 * `POST /plc/review` validates without generating anything: it parses the
 * program and reports syntax errors, undeclared or unused tags, type
 * mismatches and unreachable code, each on its line. No account is needed —
 * nothing is stored and no model is called.
 */

export type ReviewOutcome =
  | { kind: 'reviewed'; result: PlcValidationResult }
  /** The program is empty or over the size limit; the server said which. */
  | { kind: 'rejected'; detail: string | null }
  | { kind: 'failed' };

export interface ReviewOptions {
  source: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

export async function reviewPlc(options: ReviewOptions): Promise<ReviewOutcome> {
  const { source, fetchImpl = fetch, endpoint = '/api/v1/plc/review' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source }),
    });
  } catch {
    return { kind: 'failed' };
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }

  if (response.status === 413 || response.status === 422) {
    const detail =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? payload.detail
        : null;
    return { kind: 'rejected', detail: typeof detail === 'string' ? detail : null };
  }
  if (!response.ok) return { kind: 'failed' };

  // Narrowed on the one field the view branches on; a response without a
  // verdict is not a review, and rendering it would show an empty banner.
  if (typeof payload !== 'object' || payload === null || !('status' in payload)) {
    return { kind: 'failed' };
  }
  return { kind: 'reviewed', result: payload as PlcValidationResult };
}
