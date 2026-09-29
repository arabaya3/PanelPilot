/**
 * Asking the API to turn a panel schedule into a schematic specification.
 *
 * Same shape as `sessions.ts`: a discriminated union rather than a throw, so
 * the page always has something truthful to render. An ambiguous schedule is
 * its own outcome, carrying the server's list of problems, because the
 * engineer fixes it by editing the schedule rather than by retrying.
 */
import type { components } from '@panelpilot/shared-types';

export type SchematicRequest = components['schemas']['SchematicRequest'];
export type SchematicSpec = components['schemas']['SchematicSpec'];

export type BuildOutcome =
  | { kind: 'built'; spec: SchematicSpec }
  /** The schedule cannot be drawn as given; `message` says why. */
  | { kind: 'invalid'; message: string }
  | { kind: 'unavailable' }
  | { kind: 'failed' };

export interface BuildOptions {
  token: string;
  schedule: SchematicRequest;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/** Build the specification for a schedule. */
export async function buildSchematic(options: BuildOptions): Promise<BuildOutcome> {
  const { token, schedule, fetchImpl = fetch, endpoint = '/api/v1/schematics' } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(schedule),
    });
  } catch {
    return { kind: 'failed' };
  }

  if (response.status === 404) return { kind: 'unavailable' };
  if (response.status === 422) return { kind: 'invalid', message: await errorMessage(response) };
  if (!response.ok) return { kind: 'failed' };

  const payload: unknown = await response.json().catch(() => null);
  return isSpec(payload) ? { kind: 'built', spec: payload } : { kind: 'failed' };
}

/**
 * The server's explanation of a refused schedule.
 *
 * A domain refusal carries `detail` as a string; a schema refusal carries a
 * list of field errors. Both are flattened to one line the engineer can read.
 */
async function errorMessage(response: Response): Promise<string> {
  const body: unknown = await response.json().catch(() => null);
  if (typeof body !== 'object' || body === null) return '';
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: unknown) => {
        if (typeof item !== 'object' || item === null) return '';
        const { loc, msg } = item as { loc?: unknown; msg?: unknown };
        const where = Array.isArray(loc) ? loc.filter((p) => p !== 'body').join('.') : '';
        return typeof msg === 'string' ? (where ? `${where}: ${msg}` : msg) : '';
      })
      .filter((line) => line !== '')
      .join('; ');
  }
  return '';
}

/**
 * Check the fields the renderer reads before trusting a payload.
 *
 * Narrowed rather than cast: a renderer handed a payload missing its
 * connections would draw devices with no wiring, which looks like a design.
 */
function isSpec(payload: unknown): payload is SchematicSpec {
  if (typeof payload !== 'object' || payload === null) return false;
  const p = payload as Record<string, unknown>;
  return (
    typeof p.title === 'string' &&
    typeof p.supply === 'string' &&
    typeof p.incomer === 'string' &&
    Array.isArray(p.components) &&
    Array.isArray(p.connections) &&
    Array.isArray(p.groups) &&
    Array.isArray(p.unknown_kinds) &&
    typeof p.enclosure === 'object' &&
    typeof p.trunking === 'object'
  );
}
