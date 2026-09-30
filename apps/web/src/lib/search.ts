import type { components } from '@panelpilot/shared-types';

type RetrievedPassage = components['schemas']['RetrievedPassage'];

export type SearchOutcome =
  | { kind: 'loaded'; passages: RetrievedPassage[] }
  | { kind: 'unauthorized' }
  /** Staging asked for without the reviewer role. */
  | { kind: 'forbidden' }
  | { kind: 'rate-limited' }
  /** Retrieval is down; the query was fine and may work if repeated. */
  | { kind: 'unavailable' }
  | { kind: 'failed' };

/**
 * Search the documentation corpus: `POST /api/v1/search`.
 *
 * Production -- what answers cite -- unless a reviewer asks for staging.
 */
export async function searchDocuments(options: {
  token: string;
  query: string;
  manufacturers?: string[];
  corpus?: 'production' | 'staging';
  topK?: number;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<SearchOutcome> {
  const {
    token,
    query,
    manufacturers = [],
    corpus = 'production',
    topK = 10,
    fetchImpl = fetch,
    endpoint = '/api/v1/search',
  } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        query,
        corpus,
        top_k: topK,
        filters: manufacturers.length > 0 ? { manufacturers } : null,
      }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  if (response.status === 403) return { kind: 'forbidden' };
  if (response.status === 429) return { kind: 'rate-limited' };
  if (response.status === 503) return { kind: 'unavailable' };
  if (!response.ok) return { kind: 'failed' };

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }
  if (typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  const { passages } = payload as { passages?: unknown };
  if (!Array.isArray(passages)) return { kind: 'failed' };
  return { kind: 'loaded', passages: passages as RetrievedPassage[] };
}

/** Where a passage came from: its document, at its page when known. */
export function passageSourceUrl(passage: RetrievedPassage): string | null {
  const url = passage.citation.document_id;
  if (!url) return null;
  return typeof passage.citation.page === 'number'
    ? `${url}#page=${String(passage.citation.page)}`
    : url;
}
