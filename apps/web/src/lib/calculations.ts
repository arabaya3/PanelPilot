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

export type VfdSelectionRequest = components['schemas']['VfdSelectionRequest'];
export type VfdSelectionResponse = components['schemas']['VfdSelectionResponse'];

export type VfdSelectionOutcome =
  | { kind: 'selected'; response: VfdSelectionResponse }
  | { kind: 'refused'; detail: string }
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
  if (response.status === 422) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    return { kind: 'refused', detail: typeof detail === 'string' ? detail : '' };
  }
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
  | { kind: 'refused'; detail: string }
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
  if (response.status === 422) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    return { kind: 'refused', detail: typeof detail === 'string' ? detail : '' };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('result' in payload) || !('sources' in payload)) return { kind: 'failed' };
  return { kind: 'built', response: payload as PanelBomResponse };
}
