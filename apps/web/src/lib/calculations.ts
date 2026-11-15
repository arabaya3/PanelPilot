import type { components } from '@panelpilot/shared-types';

/**
 * A calculation refusing its input (a 422).
 *
 * `detail` is the server's English, kept for logs and tests but never shown:
 * it can name internal fields (`design_current_a must be positive`). A page
 * says the refusal from `code` and `params` when the server sent them, marks
 * the inputs named in `fields` (the request fields a schema check rejected),
 * and otherwise falls back to its own generic sentence.
 */
export type CalcRefusal = {
  kind: 'refused';
  detail: string;
  code?: string;
  params?: Record<string, string>;
  fields?: string[];
};

function refusalOf(payload: unknown): CalcRefusal {
  const body = (payload ?? {}) as { detail?: unknown; code?: unknown; params?: unknown };
  const refusal: CalcRefusal = {
    kind: 'refused',
    detail: typeof body.detail === 'string' ? body.detail : '',
  };
  if (typeof body.code === 'string' && body.code !== '') {
    refusal.code = body.code;
    if (typeof body.params === 'object' && body.params !== null) {
      refusal.params = Object.fromEntries(
        Object.entries(body.params).filter(
          (entry): entry is [string, string] => typeof entry[1] === 'string',
        ),
      );
    }
  }
  // FastAPI's schema errors: `[{ loc: ['body', 'loads', 0, 'current_a'], ... }]`.
  if (Array.isArray(body.detail)) {
    const fields = body.detail
      .map((item: unknown) => {
        const loc = (item as { loc?: unknown } | null)?.loc;
        if (!Array.isArray(loc)) return null;
        const last: unknown = loc[loc.length - 1];
        return typeof last === 'string' ? last : null;
      })
      .filter((field): field is string => field !== null);
    if (fields.length > 0) refusal.fields = [...new Set(fields)];
  }
  return refusal;
}

/** What is wrong with a number typed into a calculator, as a message key. */
export type NumberProblem =
  | 'required'
  | 'notANumber'
  | 'tooLarge'
  | 'notPositive'
  | 'negative'
  | 'notFraction'
  | 'notCount'
  | 'notWholePositive';

/**
 * The checks the API applies to a number regardless of the tables:
 * - `positive`: above zero (currents, lengths, voltages, powers).
 * - `nonNegative`: zero or more (altitude, heat given off).
 * - `fraction`: above 0 and at most 1 (efficiency, power factor).
 * - `finite`: any number (temperatures; the tables bound them).
 * - `count`: a whole number, 1 or more (circuits grouped).
 * - `wholePositive`: a whole number above zero (enclosure millimetres).
 */
export type NumberRule =
  'positive' | 'nonNegative' | 'fraction' | 'finite' | 'count' | 'wholePositive';

const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;

/**
 * Check a typed number before it is sent. The server stays authoritative --
 * the tables' own ranges (ambient, grouping, altitude) are its to refuse --
 * but an input no calculation could accept is caught at the field.
 *
 * @returns The problem, or `null` when the input passes.
 */
export function checkNumber(
  raw: string,
  rule: NumberRule,
  { optional = false }: { optional?: boolean } = {},
): NumberProblem | null {
  const text = raw.trim();
  if (text === '') return optional ? null : 'required';
  if (!DECIMAL.test(text)) return 'notANumber';
  const value = Number(text);
  if (!Number.isFinite(value)) return 'tooLarge';
  switch (rule) {
    case 'positive':
      return value > 0 ? null : 'notPositive';
    case 'nonNegative':
      return value >= 0 ? null : 'negative';
    case 'fraction':
      return value > 0 && value <= 1 ? null : 'notFraction';
    case 'count':
      return Number.isInteger(value) && value >= 1 ? null : 'notCount';
    case 'wholePositive':
      return Number.isInteger(value) && value > 0 ? null : 'notWholePositive';
    case 'finite':
      return null;
  }
}

export type CableSizingRequest = components['schemas']['CableSizingRequest'];
export type CableSizingResponse = components['schemas']['CableSizingResponse'];

export type CableSizingOutcome =
  | { kind: 'sized'; response: CableSizingResponse }
  /** Outside what the tables cover; see {@link CalcRefusal}. */
  | CalcRefusal
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
  if (response.status === 422) return refusalOf(payload);
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('result' in payload) || !('sources' in payload)) return { kind: 'failed' };
  return { kind: 'sized', response: payload as CableSizingResponse };
}

export type VfdSelectionRequest = components['schemas']['VfdSelectionRequest'];
export type VfdSelectionResponse = components['schemas']['VfdSelectionResponse'];

export type VfdSelectionOutcome =
  | { kind: 'selected'; response: VfdSelectionResponse }
  | CalcRefusal
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/** Select a drive: `POST /api/v1/calculations/vfd-selection`. A 422 is a refusal. */
export async function selectVfd(options: {
  token: string;
  request: VfdSelectionRequest;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<VfdSelectionOutcome> {
  const {
    token,
    request,
    fetchImpl = fetch,
    endpoint = '/api/v1/calculations/vfd-selection',
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
  if (response.status === 422) return refusalOf(payload);
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('result' in payload) || !('motor_current_a' in payload)) return { kind: 'failed' };
  return { kind: 'selected', response: payload as VfdSelectionResponse };
}

export type DriveRangeSummary = components['schemas']['DriveRangeSummary'];

export type DriveRangesOutcome =
  { kind: 'listed'; ranges: DriveRangeSummary[] } | { kind: 'unauthorized' } | { kind: 'failed' };

/** The drive series selection can choose from: `GET /api/v1/calculations/drive-ranges`. */
export async function listDriveRanges(options: {
  token: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<DriveRangesOutcome> {
  const { token, fetchImpl = fetch, endpoint = '/api/v1/calculations/drive-ranges' } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  if (!response.ok) return { kind: 'failed' };
  try {
    const payload: unknown = await response.json();
    return Array.isArray(payload)
      ? { kind: 'listed', ranges: payload as DriveRangeSummary[] }
      : { kind: 'failed' };
  } catch {
    return { kind: 'failed' };
  }
}

export type PanelBomRequest = components['schemas']['PanelBomRequest'];
export type PanelBomResponse = components['schemas']['PanelBomResponse'];

export type PanelBomOutcome =
  | { kind: 'built'; response: PanelBomResponse }
  | CalcRefusal
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/** Build a panel BOM: `POST /api/v1/calculations/panel-bom`. A 422 is a refusal. */
export async function buildBom(options: {
  token: string;
  request: PanelBomRequest;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<PanelBomOutcome> {
  const {
    token,
    request,
    fetchImpl = fetch,
    endpoint = '/api/v1/calculations/panel-bom',
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
  if (response.status === 422) return refusalOf(payload);
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('result' in payload) || !('sources' in payload)) return { kind: 'failed' };
  return { kind: 'built', response: payload as PanelBomResponse };
}
