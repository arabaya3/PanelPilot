import type { components } from '@panelpilot/shared-types';

export type ProjectDesignRequest = components['schemas']['ProjectDesignRequest'];
export type BoardDesignResponse = components['schemas']['BoardDesignResponse'];
/** A project as the API returns it, and as it is sent back to export. */
export type DesignProject = components['schemas']['DesignProject-Output'];
export type ExportFormat = components['schemas']['ExportFormat'];
export type LoadKind = components['schemas']['LoadKind'];
export type MotorStarter = components['schemas']['MotorStarter'];
export type LoadScheduleImport = components['schemas']['LoadScheduleImport'];
export type LoadScheduleSuggestion = components['schemas']['LoadScheduleSuggestion'];
export type PricingSettings = components['schemas']['PricingSettings'];
export type PriceListEntry = components['schemas']['PriceListEntry-Output'];
export type Quotation = components['schemas']['Quotation'];
export type PlcProgram = components['schemas']['PlcProgramResponse'];
export type DesignNote = components['schemas']['DesignNote'];

/**
 * How every design call can end besides success. A 400 or 422 is the design
 * refusing an input (a load no table protects, a malformed company setting)
 * -- an answer to show, with `detail` saying why, not a fault.
 */
export type Refusal = {
  kind: 'refused';
  /** The reason in English, as the server words it. */
  detail: string;
  /** A code the page can say in the reader's language, with its values. */
  code?: string;
  params?: Record<string, string>;
};
type Failure = Refusal | { kind: 'unauthorized' } | { kind: 'failed' };

export type DesignOutcome = { kind: 'designed'; response: BoardDesignResponse } | Failure;
export type ExportOutcome = { kind: 'exported'; blob: Blob; filename: string } | Failure;
export type ImportOutcome = { kind: 'imported'; result: LoadScheduleImport } | Failure;
/** `budget`: the month's model allowance is spent (a 429), not a fault. */
export type SuggestOutcome =
  { kind: 'suggested'; result: LoadScheduleSuggestion } | { kind: 'budget' } | Failure;
export type PriceOutcome = { kind: 'priced'; quotation: Quotation } | Failure;
export type PriceListOutcome = { kind: 'imported'; entries: PriceListEntry[] } | Failure;
export type PlcOutcome = { kind: 'written'; program: PlcProgram } | Failure;

/** Options every call takes, so tests can stand in for the network. */
type Transport = { token: string; fetchImpl?: typeof fetch; endpoint?: string };

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

function refusalOf(payload: unknown): Refusal {
  const refusal: Refusal = { kind: 'refused', detail: detailOf(payload) };
  const body = payload as { code?: unknown; params?: unknown } | null;
  if (typeof body?.code === 'string') {
    refusal.code = body.code;
    if (typeof body.params === 'object' && body.params !== null) {
      refusal.params = body.params as Record<string, string>;
    }
  }
  return refusal;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

/**
 * Send one request and sort its answer into success or a {@link Failure}.
 *
 * `body` is sent as JSON, or as-is when it is form data (a file upload).
 * `accept` checks a 2xx body has the fields the caller relies on; anything
 * else is `failed` rather than trusted.
 */
async function call<T>(
  transport: Transport,
  defaultEndpoint: string,
  body: unknown,
  accept: (payload: object) => T | null,
  extra?: (response: Response) => T | null,
): Promise<T | Failure> {
  const { token, fetchImpl = fetch, endpoint = defaultEndpoint } = transport;
  const isForm = body instanceof FormData;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: isForm
        ? { Authorization: `Bearer ${token}` }
        : { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: isForm ? body : JSON.stringify(body),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  const special = extra?.(response);
  if (special) return special;
  // 429 too: the design routes' per-address budget, said with a code.
  if (response.status === 400 || response.status === 422 || response.status === 429) {
    return refusalOf(await readJson(response));
  }
  if (!response.ok) return { kind: 'failed' };
  const payload = await readJson(response);
  if (typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  return accept(payload) ?? { kind: 'failed' };
}

function has(payload: object, ...keys: string[]): boolean {
  return keys.every((key) => key in payload);
}

function upload(file: Blob, filename: string): FormData {
  const body = new FormData();
  body.append('file', file, filename);
  return body;
}

/** Design a project of one or more boards: `POST /api/v1/design/project`. */
export function designProject(
  options: Transport & { request: ProjectDesignRequest },
): Promise<DesignOutcome> {
  return call(options, '/api/v1/design/project', options.request, (payload) =>
    has(payload, 'project', 'profile')
      ? { kind: 'designed', response: payload as BoardDesignResponse }
      : null,
  );
}

/** Export a project as a file: `POST /api/v1/design/export`. */
export async function exportDesign(
  options: Transport & {
    project: DesignProject;
    format: ExportFormat;
    profile?: Record<string, unknown> | null;
    pricing?: PricingSettings | null;
  },
): Promise<ExportOutcome> {
  const { token, fetchImpl = fetch, endpoint = '/api/v1/design/export' } = options;
  const { project, format, profile = null, pricing = null } = options;
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
  // 429 too: the design routes' per-address budget, said with a code.
  if (response.status === 400 || response.status === 422 || response.status === 429) {
    return refusalOf(await readJson(response));
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

/**
 * Read a consultant's load schedule (.xlsx, .csv or .pdf):
 * `POST /api/v1/design/load-schedule/import`.
 */
export function importSchedule(
  options: Transport & { file: Blob; filename: string },
): Promise<ImportOutcome> {
  return call(
    options,
    '/api/v1/design/load-schedule/import',
    upload(options.file, options.filename),
    (payload) =>
      has(payload, 'loads', 'warnings')
        ? { kind: 'imported', result: payload as LoadScheduleImport }
        : null,
  );
}

/**
 * Draft a load schedule from a plain description:
 * `POST /api/v1/design/load-schedule/suggest`. Each call is a model request,
 * so a 429 is the month's allowance spent rather than a fault.
 */
export function suggestSchedule(
  options: Transport & {
    description: string;
    supplyPhases: number;
    profile?: Record<string, unknown> | null;
  },
): Promise<SuggestOutcome> {
  const { description, supplyPhases, profile = null } = options;
  return call<SuggestOutcome>(
    options,
    '/api/v1/design/load-schedule/suggest',
    { description, supply_phases: supplyPhases, profile },
    (payload) =>
      has(payload, 'loads', 'assumptions')
        ? { kind: 'suggested', result: payload as LoadScheduleSuggestion }
        : null,
    (response) => (response.status === 429 ? { kind: 'budget' } : null),
  );
}

/** Price a designed project: `POST /api/v1/design/quotation`. */
export function priceDesign(
  options: Transport & {
    project: DesignProject;
    pricing: PricingSettings;
    profile?: Record<string, unknown> | null;
  },
): Promise<PriceOutcome> {
  const { project, pricing, profile = null } = options;
  return call(options, '/api/v1/design/quotation', { project, pricing, profile }, (payload) =>
    has(payload, 'lines', 'total') ? { kind: 'priced', quotation: payload as Quotation } : null,
  );
}

/** Read a price list (.xlsx or .csv): `POST /api/v1/design/price-list/import`. */
export function importPriceList(
  options: Transport & { file: Blob; filename: string },
): Promise<PriceListOutcome> {
  return call(
    options,
    '/api/v1/design/price-list/import',
    upload(options.file, options.filename),
    (payload) =>
      Array.isArray(payload) ? { kind: 'imported', entries: payload as PriceListEntry[] } : null,
  );
}

/** Write and check the control program for the PLC-switched circuits: `POST /api/v1/design/plc`. */
export function writePlcProgram(
  options: Transport & { project: DesignProject; profile?: Record<string, unknown> | null },
): Promise<PlcOutcome> {
  const { project, profile = null } = options;
  return call(options, '/api/v1/design/plc', { project, profile }, (payload) =>
    has(payload, 'source', 'validation')
      ? { kind: 'written', program: payload as PlcProgram }
      : null,
  );
}
