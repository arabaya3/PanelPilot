import type { components } from '@panelpilot/shared-types';

export type MarketInfo = components['schemas']['MarketInfo'];

/** Where the market the page picked, or the engineer chose, is kept. */
export const MARKET_STORAGE_KEY = 'panelpilot-market';

/**
 * The market a browser's time zone puts it in, by the IANA zones of each
 * country the design knows. Jerusalem is both Israel's zone and the one
 * Palestinian browsers in Jerusalem report, so there the browser's first
 * language decides: Arabic reads as Palestine, anything else as Israel.
 */
const ZONES: Record<string, string> = {
  'Asia/Gaza': 'PS',
  'Asia/Hebron': 'PS',
  'Asia/Amman': 'JO',
  'Asia/Riyadh': 'SA',
  'Asia/Dubai': 'AE',
  'Asia/Qatar': 'QA',
  'Asia/Kuwait': 'KW',
  'Africa/Cairo': 'EG',
  'Asia/Beirut': 'LB',
  'Asia/Baghdad': 'IQ',
};

/**
 * The market a visitor is most likely in, from their browser alone: no
 * location is asked for and nothing is sent anywhere.
 *
 * @returns A market code, or '' where the zone is none of the markets'.
 */
export function detectMarket(timeZone: string, languages: readonly string[]): string {
  if (timeZone === 'Asia/Jerusalem' || timeZone === 'Asia/Tel_Aviv') {
    return languages[0]?.toLowerCase().startsWith('ar') ? 'PS' : 'IL';
  }
  return ZONES[timeZone] ?? '';
}

/** The browser's own market: stored if chosen before, detected otherwise. */
export function readMarket(): string {
  try {
    const stored = window.localStorage.getItem(MARKET_STORAGE_KEY);
    if (stored !== null) return stored;
  } catch {
    // Storage off: detect each visit instead.
  }
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return detectMarket(
      zone,
      navigator.languages.length ? navigator.languages : [navigator.language],
    );
  } catch {
    return '';
  }
}

/** Remember the engineer's choice for the next visit. */
export function storeMarket(code: string): void {
  try {
    window.localStorage.setItem(MARKET_STORAGE_KEY, code);
  } catch {
    // Storage off: the choice holds for this visit only.
  }
}

/** Every market and its supply: `GET /api/v1/design/markets`, public. */
export async function fetchMarkets(
  fetchImpl: typeof fetch = fetch,
  endpoint = '/api/v1/design/markets',
): Promise<MarketInfo[] | null> {
  try {
    const response = await fetchImpl(endpoint, { method: 'GET' });
    if (!response.ok) return null;
    const payload: unknown = await response.json();
    return Array.isArray(payload) ? (payload as MarketInfo[]) : null;
  } catch {
    return null;
  }
}

/**
 * The company settings sent with a request, with the market the page uses.
 * A market the settings state themselves wins; none adds nothing.
 */
export function withMarket(
  profile: Record<string, unknown> | null,
  market: string,
  defaultKey: string,
): Record<string, unknown> | null {
  if (market === '' || (profile && 'market' in profile)) return profile;
  return { ...(profile ?? { key: defaultKey }), market };
}
