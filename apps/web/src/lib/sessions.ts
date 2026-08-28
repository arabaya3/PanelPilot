/**
 * Conversation history: the list, and one past conversation's turns.
 *
 * Follows the same shape as `trial.ts` — a discriminated union rather than a
 * thrown error, and every field narrowed out of the payload rather than cast.
 * The sidebar has to render *something* truthful when the list cannot be
 * loaded, and a client that throws pushes that decision into a component that
 * cannot answer it.
 *
 * Paginated by cursor rather than page number, because the list is ordered by
 * last activity and that changes while the engineer is scrolling: an offset
 * would skip or repeat a session every time a turn was added to one above it.
 * The cursor is opaque and is passed back exactly as received.
 */
import type { components } from '@panelpilot/shared-types';

type DiagnosticSession = components['schemas']['DiagnosticSession'];

/** One row in the sidebar. */
export interface SessionSummary {
  id: string;
  title: string;
  equipmentModel: string | null;
  turnCount: number;
  createdAt: string;
  updatedAt: string;
}

/** The outcome of asking for a page of history. */
export type SessionListResult =
  | { kind: 'loaded'; sessions: SessionSummary[]; nextCursor: string | null }
  /** The endpoint is not deployed. Distinct from `failed`: the sidebar says
   *  "history is unavailable" rather than offering a retry that cannot work. */
  | { kind: 'unavailable' }
  | { kind: 'failed' };

export interface ListSessionsOptions {
  token: string;
  cursor?: string | null;
  limit?: number;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * Fetch one page of the caller's conversation history.
 *
 * @param options - Auth token, optional cursor, and injectable seams.
 * @returns The page, or why it could not be loaded.
 */
export async function listSessions(options: ListSessionsOptions): Promise<SessionListResult> {
  const { token, cursor = null, limit, fetchImpl = fetch, endpoint = '/api/v1/sessions' } = options;

  const query = new URLSearchParams();
  if (cursor !== null && cursor !== '') query.set('cursor', cursor);
  if (limit !== undefined) query.set('limit', String(limit));
  const url = query.size > 0 ? `${endpoint}?${query.toString()}` : endpoint;

  let response: Response;
  try {
    response = await fetchImpl(url, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
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

  return readListPayload(payload) ?? { kind: 'failed' };
}

function readListPayload(payload: unknown): SessionListResult | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const { sessions, next_cursor: nextCursor } = payload as Record<string, unknown>;
  if (!Array.isArray(sessions)) return null;

  const rows: SessionSummary[] = [];
  for (const row of sessions) {
    const summary = readSummary(row);
    // One malformed row drops that row rather than the whole page: a sidebar
    // missing one entry is recoverable, an empty history reads as data loss.
    if (summary !== null) rows.push(summary);
  }

  return {
    kind: 'loaded',
    sessions: rows,
    nextCursor: typeof nextCursor === 'string' && nextCursor !== '' ? nextCursor : null,
  };
}

function readSummary(row: unknown): SessionSummary | null {
  if (typeof row !== 'object' || row === null) return null;
  const {
    id,
    title,
    equipment_model: equipmentModel,
    turn_count: turnCount,
    created_at: createdAt,
    updated_at: updatedAt,
  } = row as Record<string, unknown>;

  // The id is what makes a row selectable; without it the entry is decoration.
  if (typeof id !== 'string' || id === '') return null;

  return {
    id,
    title: typeof title === 'string' && title !== '' ? title : 'Untitled conversation',
    equipmentModel:
      typeof equipmentModel === 'string' && equipmentModel !== '' ? equipmentModel : null,
    turnCount: typeof turnCount === 'number' ? turnCount : 0,
    createdAt: typeof createdAt === 'string' ? createdAt : '',
    updatedAt: typeof updatedAt === 'string' ? updatedAt : '',
  };
}

/** The outcome of opening one past conversation. */
export type SessionFetchResult =
  { kind: 'loaded'; session: DiagnosticSession } | { kind: 'not-found' } | { kind: 'failed' };

export interface FetchSessionOptions {
  token: string;
  sessionId: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * Fetch one conversation and its turns.
 *
 * The same path the chat view uses on load, which is what the spec asks for:
 * selecting a history entry must hydrate through the existing session-fetch
 * route rather than a second, separately-maintained one that could disagree
 * with it about what a stored turn looks like.
 *
 * @param options - Auth token, session id, and injectable seams.
 * @returns The conversation, or why it could not be loaded.
 */
export async function fetchSession(options: FetchSessionOptions): Promise<SessionFetchResult> {
  const { token, sessionId, fetchImpl = fetch, endpoint = '/api/v1/diagnostics' } = options;

  let response: Response;
  try {
    response = await fetchImpl(`${endpoint}/${encodeURIComponent(sessionId)}`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    return { kind: 'failed' };
  }

  // 404 covers both "no such session" and "not yours" — the API answers the
  // same way for each on purpose, so that an id someone cannot read cannot be
  // probed for existence. The client keeps that distinction closed.
  if (response.status === 404) return { kind: 'not-found' };
  if (!response.ok) return { kind: 'failed' };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }

  if (typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  const { id, turns } = payload as Record<string, unknown>;
  if (typeof id !== 'string' || !Array.isArray(turns)) return { kind: 'failed' };

  return { kind: 'loaded', session: payload as DiagnosticSession };
}
