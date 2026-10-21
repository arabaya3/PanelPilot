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

/**
 * Words too common to say anything about a match: a passage sharing only
 * "the" or "how" with a search does not contain what was searched for.
 */
const STOPWORDS = new Set([
  'about',
  'all',
  'and',
  'any',
  'are',
  'but',
  'can',
  'does',
  'for',
  'from',
  'has',
  'have',
  'how',
  'into',
  'its',
  'not',
  'off',
  'out',
  'that',
  'the',
  'then',
  'this',
  'too',
  'was',
  'what',
  'when',
  'where',
  'which',
  'who',
  'why',
  'will',
  'with',
  'you',
  'your',
]);

const WORD = /[\p{L}\p{N}]+/gu;

/** A word that can identify a match: three letters or more, or holding a digit (F30001, S7). */
function significant(word: string): boolean {
  return !STOPWORDS.has(word) && (word.length >= 3 || /\p{N}/u.test(word));
}

/**
 * The searched-for words a passage contains, in the order they were typed.
 *
 * Search ranks every passage it returns; it also always returns the nearest
 * passages by meaning, even for a search the manuals have nothing on. Whether
 * a passage contains the words searched for is a plain fact the reader can
 * check, unlike the ranking score, so it is what the page says about each
 * result. Words, not substrings: "F0001" is not found in "F00010".
 *
 * @param query - What was searched for.
 * @param text - The passage.
 * @returns The query's significant words found in the passage, lower-cased.
 */
export function matchedTerms(query: string, text: string): string[] {
  const words = new Set((text.toLowerCase().match(WORD) ?? []).filter(significant).map(singular));
  const found: string[] = [];
  for (const word of query.toLowerCase().match(WORD) ?? []) {
    if (significant(word) && words.has(singular(word)) && !found.includes(word)) found.push(word);
  }
  return found;
}

/** "faults" and "fault" are the same word to a reader; folded on both sides. */
function singular(word: string): string {
  return word.length > 3 && word.endsWith('s') && !word.endsWith('ss') ? word.slice(0, -1) : word;
}
