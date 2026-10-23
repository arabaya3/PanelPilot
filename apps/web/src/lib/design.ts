import type { components } from '@panelpilot/shared-types';

export type ProjectDesignRequest = components['schemas']['ProjectDesignRequest-Input'];
/** A saved request as the server returns it: numbers as strings, defaults filled. */
export type SavedRequest = components['schemas']['ProjectDesignRequest-Output'];
export type BoardDesignResponse = components['schemas']['BoardDesignResponse'];
/** A project as the API returns it, and as it is sent back to export. */
export type DesignProject = components['schemas']['DesignProject-Output'];
export type ExportFormat = components['schemas']['ExportFormat'];

/** The code the API refuses a format with whose draft mark an import would strip. */
export const EXPORT_FORMAT_UNPROTECTED = 'export_format_unprotected';

/**
 * The formats the API does not export yet (`export_format_unprotected`), as a
 * draft or as a revision: every CSV, QElectroTech, AutomationML and the PLC
 * program. Each carries its draft mark only as a comment, which a normal
 * import strips. Mirrors `app.domain.design.UNPROTECTED_FORMATS`.
 */
export const UNPROTECTED_FORMATS: ReadonlySet<ExportFormat> = new Set<ExportFormat>([
  'devices_csv',
  'parts_csv',
  'cables_csv',
  'circuits_csv',
  'terminals_csv',
  'eplan_devices_csv',
  'ace_components_csv',
  'ace_terminals_csv',
  'quotation_csv',
  'plc_io_csv',
  'qet',
  'aml',
  'plc_st',
]);

/** Whether the API refuses to export `format` until its draft mark survives import. */
export function isUnprotected(format: ExportFormat): boolean {
  return UNPROTECTED_FORMATS.has(format);
}
export type LoadKind = components['schemas']['LoadKind'];
export type MotorStarter = components['schemas']['MotorStarter'];
export type LoadScheduleImport = components['schemas']['LoadScheduleImport'];
export type LoadScheduleSuggestion = components['schemas']['LoadScheduleSuggestion'];
export type PricingSettings = components['schemas']['PricingSettings'];
export type PriceListEntry = components['schemas']['PriceListEntry-Output'];
export type Quotation = components['schemas']['Quotation'];
export type DesignNote = components['schemas']['DesignNote'];
export type SavedProject = components['schemas']['SavedProject'];
export type ProjectPage = components['schemas']['ProjectPage'];
export type MarkupReport = components['schemas']['MarkupReport'];
export type MarkupSuggestion = components['schemas']['MarkupSuggestion'];

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
  /**
   * The params as the server sent them, lists included, for a refusal whose
   * values are not all a sentence's strings (what an execution export lacks).
   */
  values?: Record<string, unknown>;
  /** Fields the server does not accept (a 422 `extra_forbidden`), as paths. */
  unknownFields?: string[];
  /** Text fields longer than the server accepts (a 422 `string_too_long`), as paths. */
  tooLong?: TooLong[];
  /** Numbers above what the server accepts (a 422 `less_than_equal`), as paths. */
  tooLarge?: TooLarge[];
};

/** A number the server refused as too large, and the most it takes. */
export type TooLarge = { path: string; max: string };

/** A text field the server refused as too long, and the most it takes. */
export type TooLong = { path: string; max: number };
type Failure = Refusal | { kind: 'unauthorized' } | { kind: 'failed' };

export type DesignOutcome = { kind: 'designed'; response: BoardDesignResponse } | Failure;
export type ExportOutcome = { kind: 'exported'; blob: Blob; filename: string } | Failure;
export type ImportOutcome = { kind: 'imported'; result: LoadScheduleImport } | Failure;
/** `budget`: the month's model allowance is spent, not a fault. */
export type SuggestOutcome =
  { kind: 'suggested'; result: LoadScheduleSuggestion } | { kind: 'budget' } | Failure;
export type PriceOutcome = { kind: 'priced'; quotation: Quotation } | Failure;
export type PriceListOutcome = { kind: 'imported'; entries: PriceListEntry[] } | Failure;
export type ProjectOutcome = { kind: 'saved'; project: SavedProject } | Failure;
export type ProjectListOutcome = { kind: 'listed'; page: ProjectPage } | Failure;
export type DeleteOutcome = { kind: 'deleted' } | Failure;
export type MarkupOutcome = { kind: 'read'; report: MarkupReport } | Failure;
export type CompanySettings = components['schemas']['CompanySettings'];
export type SettingsOutcome = { kind: 'settings'; saved: CompanySettings } | Failure;

/** Statuses whose body is a refusal to say, not a fault. */
const REFUSED = [400, 403, 404, 409, 422, 429];

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

/**
 * The fields a strict schema refused as unknown, from FastAPI's 422 detail:
 * `["body", "project", "boards", 0, "standby_supply"]` is said
 * `project.boards[0].standby_supply`, so the engineer can find it.
 */
export function unknownFieldsOf(payload: unknown): string[] {
  const fields: string[] = [];
  for (const { path } of errorsOfType(payload, 'extra_forbidden')) {
    if (!fields.includes(path)) fields.push(path);
  }
  return fields;
}

/** The text fields a 422 refused as too long, with the most each takes. */
export function tooLongOf(payload: unknown): TooLong[] {
  return errorsOfType(payload, 'string_too_long').flatMap(({ path, ctx }) => {
    const max = Number(ctx.max_length);
    return Number.isInteger(max) && max > 0 ? [{ path, max }] : [];
  });
}

/** The numbers a 422 refused as above their limit, with the limit. */
export function tooLargeOf(payload: unknown): TooLarge[] {
  return errorsOfType(payload, 'less_than_equal').flatMap(({ path, ctx }) => {
    const max = ctx.le;
    return typeof max === 'number' || typeof max === 'string' ? [{ path, max: String(max) }] : [];
  });
}

/**
 * A 422's validation errors of one type, each with its body path. FastAPI
 * lists them in `detail`; an envelope may carry them in `errors` instead.
 */
function errorsOfType(
  payload: unknown,
  type: string,
): Array<{ path: string; ctx: Record<string, unknown> }> {
  const body = payload as { detail?: unknown; errors?: unknown } | null;
  const detail = Array.isArray(body?.detail)
    ? body.detail
    : Array.isArray(body?.errors)
      ? body.errors
      : [];
  const found: Array<{ path: string; ctx: Record<string, unknown> }> = [];
  for (const item of detail as unknown[]) {
    const entry = item as { type?: unknown; loc?: unknown; ctx?: unknown } | null;
    if (entry?.type !== type || !Array.isArray(entry.loc)) continue;
    const parts = (entry.loc as unknown[]).filter(
      (part, index): part is string | number =>
        (typeof part === 'string' || typeof part === 'number') && !(index === 0 && part === 'body'),
    );
    const path = parts
      .map((part, index) =>
        typeof part === 'number' ? `[${String(part)}]` : index === 0 ? part : `.${part}`,
      )
      .join('');
    const ctx =
      typeof entry.ctx === 'object' && entry.ctx !== null
        ? (entry.ctx as Record<string, unknown>)
        : {};
    if (path !== '') found.push({ path, ctx });
  }
  return found;
}

function refusalOf(payload: unknown): Refusal {
  const refusal: Refusal = { kind: 'refused', detail: detailOf(payload) };
  const body = payload as { code?: unknown; params?: unknown } | null;
  if (typeof body?.code === 'string') {
    refusal.code = body.code;
    if (typeof body.params === 'object' && body.params !== null && !Array.isArray(body.params)) {
      const values = body.params as Record<string, unknown>;
      refusal.values = values;
      // Only scalars go into a sentence; a list is read from `values`.
      refusal.params = Object.fromEntries(
        Object.entries(values)
          .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
          .map(([key, value]) => [key, String(value)]),
      );
    }
  }
  const unknown = unknownFieldsOf(payload);
  if (unknown.length > 0) refusal.unknownFields = unknown;
  const long = tooLongOf(payload);
  if (long.length > 0) refusal.tooLong = long;
  const large = tooLargeOf(payload);
  if (large.length > 0) refusal.tooLarge = large;
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
 * `body` is sent as JSON, or as-is when it is form data (a file upload); a
 * GET or DELETE sends none. `accept` checks a 2xx body has the fields the
 * caller relies on; anything else is `failed` rather than trusted.
 */
async function call<T>(
  transport: Transport,
  defaultEndpoint: string,
  body: unknown,
  accept: (payload: object) => T | null,
  extra?: (response: Response) => T | null,
  method: 'POST' | 'PUT' | 'GET' | 'DELETE' = 'POST',
): Promise<T | Failure> {
  const { token, fetchImpl = fetch, endpoint = defaultEndpoint } = transport;
  const isForm = body instanceof FormData;
  const auth = { Authorization: `Bearer ${token}` };
  let response: Response;
  try {
    response = await fetchImpl(
      endpoint,
      method === 'GET' || method === 'DELETE'
        ? { method, headers: auth }
        : {
            method,
            headers: isForm ? auth : { 'Content-Type': 'application/json', ...auth },
            body: isForm ? body : JSON.stringify(body),
          },
    );
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  const special = extra?.(response);
  if (special) return special;
  // 429 too: the design routes' per-address budget, said with a code; 404, a
  // saved project that is not (or no longer) there; and 409, a revision that
  // cannot be approved or exported as it stands (`revision_stale`, ...).
  if (REFUSED.includes(response.status)) {
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

/**
 * The name a download is saved under: the project's own (RFC 6266
 * `filename*`, which keeps an Arabic name), else the ASCII `filename`.
 */
export function savedName(disposition: string): string | null {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded.trim());
    } catch {
      // A malformed encoding falls back to the plain name.
    }
  }
  return /filename="([^"]+)"/.exec(disposition)?.[1] ?? null;
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
  if (REFUSED.includes(response.status)) return refusalOf(await readJson(response));
  return download(response, format);
}

/** A 2xx export response as the file it carries. */
async function download(response: Response, format: ExportFormat): Promise<ExportOutcome> {
  if (!response.ok) return { kind: 'failed' };
  const disposition = response.headers.get('Content-Disposition') ?? '';
  try {
    return {
      kind: 'exported',
      blob: await response.blob(),
      filename: savedName(disposition) ?? `project.${format}`,
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

/** The code the server gives a 429 when the month's model calls are spent. */
export const MODEL_BUDGET_EXCEEDED = 'model_budget_exceeded';

/**
 * Draft a load schedule from a plain description:
 * `POST /api/v1/design/load-schedule/suggest`.
 *
 * Two different limits answer this route with a 429, and only the code tells
 * them apart: the month's model allowance (`budget` -- nothing to do but wait
 * for next month or change plan) and the per-address design limit
 * (`rate_limited` -- wait a few minutes). Reading every 429 as the first told
 * an engineer their month was gone when a short wait would have done.
 */
export async function suggestSchedule(
  options: Transport & {
    description: string;
    supplyPhases: number;
    profile?: Record<string, unknown> | null;
  },
): Promise<SuggestOutcome> {
  const { description, supplyPhases, profile = null } = options;
  const outcome = await call<SuggestOutcome>(
    options,
    '/api/v1/design/load-schedule/suggest',
    { description, supply_phases: supplyPhases, profile },
    (payload) =>
      has(payload, 'loads', 'assumptions')
        ? { kind: 'suggested', result: payload as LoadScheduleSuggestion }
        : null,
  );
  return outcome.kind === 'refused' && outcome.code === MODEL_BUDGET_EXCEEDED
    ? { kind: 'budget' }
    : outcome;
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

const PROJECTS = '/api/v1/design/projects';

function saved(payload: object): ProjectOutcome | null {
  return has(payload, 'id', 'revisions', 'request')
    ? { kind: 'saved', project: payload as SavedProject }
    : null;
}

/** The caller's saved projects, newest first: `GET /api/v1/design/projects`. */
export function listProjects(
  options: Transport & { cursor?: string | null },
): Promise<ProjectListOutcome> {
  const query = options.cursor ? `?cursor=${encodeURIComponent(options.cursor)}` : '';
  return call<ProjectListOutcome>(
    options,
    `${PROJECTS}${query}`,
    undefined,
    (payload) =>
      has(payload, 'projects') ? { kind: 'listed', page: payload as ProjectPage } : null,
    undefined,
    'GET',
  );
}

/** Save a new project as its first revision: `POST /api/v1/design/projects`. */
export function saveProject(
  options: Transport & { name: string; request: ProjectDesignRequest; note?: string },
): Promise<ProjectOutcome> {
  const { name, request, note = '' } = options;
  return call(options, PROJECTS, { name, request, note }, saved);
}

/** Save a new revision of a project: `POST /api/v1/design/projects/{id}/revisions`. */
export function reviseProject(
  options: Transport & { id: string; request: ProjectDesignRequest; note?: string },
): Promise<ProjectOutcome> {
  const { id, request, note = '' } = options;
  return call(options, `${PROJECTS}/${encodeURIComponent(id)}/revisions`, { request, note }, saved);
}

/** Open a project, at its latest revision or one asked for. */
export function openProject(
  options: Transport & { id: string; revision?: number | null },
): Promise<ProjectOutcome> {
  const { id, revision } = options;
  const query = revision ? `?revision=${String(revision)}` : '';
  return call<ProjectOutcome>(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}${query}`,
    undefined,
    saved,
    undefined,
    'GET',
  );
}

/**
 * What a revision is exported for. `review` gives the study set, marked
 * draft or review-approved from the stored approval; `execution` is refused
 * until an execution package can be checked complete, and names what it lacks.
 */
export type ExportPurpose = 'review' | 'execution';

/**
 * Export a saved revision as the server stored and validated it:
 * `POST /api/v1/design/projects/{id}/revisions/{number}/export`. Nothing of
 * the page is sent, so the file is the revision, not what was typed since.
 */
export async function exportRevision(
  options: Transport & {
    id: string;
    revision: number;
    format: ExportFormat;
    purpose?: ExportPurpose;
    pricing?: PricingSettings | null;
  },
): Promise<ExportOutcome> {
  const { id, revision, format, purpose = 'review', pricing = null } = options;
  const {
    token,
    fetchImpl = fetch,
    endpoint = `${PROJECTS}/${encodeURIComponent(id)}/revisions/${String(revision)}/export`,
  } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ format, purpose, ...(pricing ? { pricing } : {}) }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  if (REFUSED.includes(response.status)) return refusalOf(await readJson(response));
  return download(response, format);
}

/** Approve one revision, once, by the engineer's name for the title block. */
export function approveRevision(
  options: Transport & { id: string; revision: number; approver: string },
): Promise<ProjectOutcome> {
  const { id, revision, approver } = options;
  return call(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}/revisions/${String(revision)}/approval`,
    { approver },
    saved,
  );
}

const COMPANY_SETTINGS = '/api/v1/design/company-settings';

function settingsOf(payload: object): SettingsOutcome | null {
  return has(payload, 'settings') ? { kind: 'settings', saved: payload as CompanySettings } : null;
}

/** The company's saved settings: `GET /api/v1/design/company-settings`. */
export function getCompanySettings(options: Transport): Promise<SettingsOutcome> {
  return call<SettingsOutcome>(options, COMPANY_SETTINGS, undefined, settingsOf, undefined, 'GET');
}

/** Save the company's settings for every later project: `PUT .../company-settings`. */
export function saveCompanySettings(
  options: Transport & { settings: Record<string, unknown> },
): Promise<SettingsOutcome> {
  return call<SettingsOutcome>(
    options,
    COMPANY_SETTINGS,
    { settings: options.settings },
    settingsOf,
    undefined,
    'PUT',
  );
}

/** Delete a project and all its revisions. */
export function deleteProject(options: Transport & { id: string }): Promise<DeleteOutcome> {
  return call<DeleteOutcome>(
    options,
    `${PROJECTS}/${encodeURIComponent(options.id)}`,
    undefined,
    () => null,
    (response) => (response.status === 204 ? { kind: 'deleted' } : null),
    'DELETE',
  );
}

/**
 * Read a reviewer's marks off a drawing set PDF, placed on this project's
 * drawings: `POST /api/v1/design/markups`.
 */
export function readMarkups(
  options: Transport & {
    file: Blob;
    filename: string;
    project: DesignProject;
    profile?: Record<string, unknown> | null;
  },
): Promise<MarkupOutcome> {
  const body = upload(options.file, options.filename);
  body.append('project', JSON.stringify(options.project));
  if (options.profile) body.append('profile', JSON.stringify(options.profile));
  return call(options, '/api/v1/design/markups', body, (payload) =>
    has(payload, 'markups', 'matched') ? { kind: 'read', report: payload as MarkupReport } : null,
  );
}
