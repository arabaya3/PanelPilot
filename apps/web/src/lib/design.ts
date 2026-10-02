import type { components } from '@panelpilot/shared-types';

export type BoardDesignRequest = components['schemas']['BoardDesignRequest'];
export type BoardDesignResponse = components['schemas']['BoardDesignResponse'];
/** A project as the API returns it, and as it is sent back to export. */
export type DesignProject = components['schemas']['DesignProject-Output'];
export type ExportFormat = components['schemas']['ExportFormat'];
export type LoadKind = components['schemas']['LoadKind'];

export type DesignOutcome =
  | { kind: 'designed'; response: BoardDesignResponse }
  /** The schedule or the company settings were refused; `detail` says why. */
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

function detailOf(payload: unknown): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item as { msg?: unknown }).msg)
      .filter((msg): msg is string => typeof msg === 'string')
      .join('; ');
  }
  return '';
}

/**
 * Design a distribution board: `POST /api/v1/design/distribution-board`.
 *
 * A 400 or 422 is the design refusing an input (a load no table protects, a
 * malformed company setting) -- an answer to show, not a fault.
 */
export async function designBoard(options: {
  token: string;
  request: BoardDesignRequest;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<DesignOutcome> {
  const {
    token,
    request,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/distribution-board',
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
  if (response.status === 400 || response.status === 422) {
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('project' in payload) || !('profile' in payload)) return { kind: 'failed' };
  return { kind: 'designed', response: payload as BoardDesignResponse };
}

export type ExportOutcome =
  | { kind: 'exported'; blob: Blob; filename: string }
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/** Export a project as a file: `POST /api/v1/design/export`. */
export async function exportDesign(options: {
  token: string;
  project: DesignProject;
  format: ExportFormat;
  profile?: Record<string, unknown> | null;
  pricing?: PricingSettings | null;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<ExportOutcome> {
  const {
    token,
    project,
    format,
    profile = null,
    pricing = null,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/export',
  } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ project, format, profile, pricing }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  if (response.status === 400 || response.status === 422) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      // A refusal without a body still refuses.
    }
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok) return { kind: 'failed' };
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const match = /filename="([^"]+)"/.exec(disposition);
  try {
    return {
      kind: 'exported',
      blob: await response.blob(),
      filename: match?.[1] ?? `project.${format}`,
    };
  } catch {
    return { kind: 'failed' };
  }
}

export type LoadScheduleImport = components['schemas']['LoadScheduleImport'];

export type ImportOutcome =
  | { kind: 'imported'; result: LoadScheduleImport }
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/**
 * Read a consultant's load schedule (.xlsx, .csv or .pdf):
 * `POST /api/v1/design/load-schedule/import`.
 */
export async function importSchedule(options: {
  token: string;
  file: Blob;
  filename: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<ImportOutcome> {
  const {
    token,
    file,
    filename,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/load-schedule/import',
  } = options;
  const body = new FormData();
  body.append('file', file, filename);
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body,
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
  if (response.status === 400 || response.status === 422) {
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('loads' in payload) || !('warnings' in payload)) return { kind: 'failed' };
  return { kind: 'imported', result: payload as LoadScheduleImport };
}

export type LoadScheduleSuggestion = components['schemas']['LoadScheduleSuggestion'];

export type SuggestOutcome =
  | { kind: 'suggested'; result: LoadScheduleSuggestion }
  | { kind: 'refused'; detail: string }
  | { kind: 'budget' }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/**
 * Draft a load schedule from a plain description:
 * `POST /api/v1/design/load-schedule/suggest`. Each call is a model request,
 * so a 429 is the month's allowance spent rather than a fault.
 */
export async function suggestSchedule(options: {
  token: string;
  description: string;
  supplyPhases: number;
  profile?: Record<string, unknown> | null;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<SuggestOutcome> {
  const {
    token,
    description,
    supplyPhases,
    profile = null,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/load-schedule/suggest',
  } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ description, supply_phases: supplyPhases, profile }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  if (response.status === 429) return { kind: 'budget' };
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 400 || response.status === 422) {
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('loads' in payload) || !('assumptions' in payload)) return { kind: 'failed' };
  return { kind: 'suggested', result: payload as LoadScheduleSuggestion };
}

export type PricingSettings = components['schemas']['PricingSettings'];
export type PriceListEntry = components['schemas']['PriceListEntry-Output'];
export type Quotation = components['schemas']['Quotation'];

export type PriceOutcome =
  | { kind: 'priced'; quotation: Quotation }
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/** Price a designed project: `POST /api/v1/design/quotation`. */
export async function priceDesign(options: {
  token: string;
  project: DesignProject;
  pricing: PricingSettings;
  profile?: Record<string, unknown> | null;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<PriceOutcome> {
  const {
    token,
    project,
    pricing,
    profile = null,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/quotation',
  } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ project, pricing, profile }),
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
  if (response.status === 400 || response.status === 422) {
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('lines' in payload) || !('total' in payload)) return { kind: 'failed' };
  return { kind: 'priced', quotation: payload as Quotation };
}

export type PriceListOutcome =
  | { kind: 'imported'; entries: PriceListEntry[] }
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/** Read a price list (.xlsx or .csv): `POST /api/v1/design/price-list/import`. */
export async function importPriceList(options: {
  token: string;
  file: Blob;
  filename: string;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<PriceListOutcome> {
  const {
    token,
    file,
    filename,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/price-list/import',
  } = options;
  const body = new FormData();
  body.append('file', file, filename);
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body,
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
  if (response.status === 400 || response.status === 422) {
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok || !Array.isArray(payload)) return { kind: 'failed' };
  return { kind: 'imported', entries: payload as PriceListEntry[] };
}

export type PlcProgram = components['schemas']['PlcProgramResponse'];

export type PlcOutcome =
  | { kind: 'written'; program: PlcProgram }
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/** Write and check the control program for the PLC-switched circuits: `POST /api/v1/design/plc`. */
export async function writePlcProgram(options: {
  token: string;
  project: DesignProject;
  profile?: Record<string, unknown> | null;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<PlcOutcome> {
  const {
    token,
    project,
    profile = null,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/plc',
  } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ project, profile }),
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
  if (response.status === 400 || response.status === 422) {
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('source' in payload) || !('validation' in payload)) return { kind: 'failed' };
  return { kind: 'written', program: payload as PlcProgram };
}
