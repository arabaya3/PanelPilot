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
/**
 * A provisional estimate of the design on the page: priced and checked on the
 * server, never a quotation (that is a saved revision's `quotation_pdf`).
 */
export type Quotation = components['schemas']['QuotationEstimate'];
export type DesignNote = components['schemas']['DesignNote'];
/** Whether the opened revision's configuration can be carried (M7b §4). */
export type ConfigurationStatus = components['schemas']['SavedProject']['configuration_status'];
export type SavedProject = components['schemas']['SavedProject'];
export type ProjectPage = components['schemas']['ProjectPage'];
/** An action the server lets the caller take; it checks again on every request. */
export type Permission = components['schemas']['Permission'];

/**
 * Whether a response says the caller may take an action. A response that
 * names none (an older API) reads as not allowed: the page only hides or
 * disables, and the server decides.
 */
export function permits(
  subject: { permissions?: Permission[] } | null | undefined,
  permission: Permission,
): boolean {
  return subject?.permissions?.includes(permission) ?? false;
}
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
  /** Every field a 422 named, whatever it objected to, as paths: said where the reason is not. */
  invalidFields?: string[];
  /** For a 429: the seconds the server's `Retry-After` asks the page to wait. */
  retryAfterSeconds?: number;
};

/** A number the server refused as too large, and the most it takes. */
export type TooLarge = { path: string; max: string };

/** A text field the server refused as too long, and the most it takes. */
export type TooLong = { path: string; max: number };
/**
 * A 401: the session is not (or no longer) valid. `code: 'account_inactive'`
 * when the server says the account was removed from the team or deactivated
 * (it answers with that code), which no sign-in renewal fixes.
 */
type Failure = Refusal | { kind: 'unauthorized'; code?: string } | { kind: 'failed' };

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
/** What a configuration would compose to against a project's latest revision; nothing saved. */
export type ConfigurationReview = components['schemas']['ConfigurationReview'];
/** A `pp-design/2` model as a review shows it (a dry run, or an opened v2 revision). */
export type V2ModelSummary = components['schemas']['V2ModelSummary'];
export type DesignFinding = components['schemas']['DesignFinding'];
export type V2Finding = components['schemas']['V2Finding'];
export type V2Value = components['schemas']['V2Value'];
export type ConfigurationOutcome = { kind: 'reviewed'; review: ConfigurationReview } | Failure;

/**
 * The review-only documents a `pp-design/2` revision exports (PDF, NOT FOR EXECUTION):
 * the terminal and wire schedules and the connection drawing.
 */
export const SCHEDULE_FORMATS = [
  'terminal_schedule_pdf',
  'wire_schedule_pdf',
  'connection_drawing_pdf',
] as const satisfies readonly ExportFormat[];

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
  type: string | null,
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
    if (entry === null || (type !== null && entry.type !== type) || !Array.isArray(entry.loc)) {
      continue;
    }
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

/** Every field a 422 named, once each, in the order named. */
export function invalidFieldsOf(payload: unknown): string[] {
  return [...new Set(errorsOfType(payload, null).map(({ path }) => path))];
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
  const invalid = invalidFieldsOf(payload);
  if (invalid.length > 0) refusal.invalidFields = invalid;
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
  /** Further statuses whose body is a coded refusal (the AI routes' 502, 503 and 504). */
  alsoRefused: readonly number[] = [],
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
  if (response.status === 401) {
    const body = (await readJson(response)) as { code?: unknown } | null;
    return body?.code === 'account_inactive'
      ? { kind: 'unauthorized', code: 'account_inactive' }
      : { kind: 'unauthorized' };
  }
  const special = extra?.(response);
  if (special) return special;
  // 429 too: the design routes' per-address budget, said with a code; 404, a
  // saved project that is not (or no longer) there; and 409, a revision that
  // cannot be approved or exported as it stands (`revision_stale`, ...).
  if (REFUSED.includes(response.status) || alsoRefused.includes(response.status)) {
    const refusal = refusalOf(await readJson(response));
    const wait = Number(response.headers.get('Retry-After'));
    return response.status === 429 && Number.isFinite(wait) && wait > 0
      ? { ...refusal, retryAfterSeconds: wait }
      : refusal;
  }
  if (!response.ok) return { kind: 'failed' };
  const payload = await readJson(response);
  if (typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  return accept(payload) ?? { kind: 'failed' };
}

function has(payload: object, ...keys: string[]): boolean {
  return keys.every((key) => key in payload);
}

/** The longest name a download is saved under, in characters. */
const NAME_LIMIT = 120;

/**
 * A server-given name made safe to save under: no path separators, no
 * characters a file system refuses, no control characters, no `..`, no
 * leading or trailing dots or spaces, and at most `NAME_LIMIT` characters
 * (keeping the extension). Null when nothing usable is left.
 */
export function safeFileName(name: string): string | null {
  const cleaned = name
    // eslint-disable-next-line no-control-regex -- control characters are what is removed
    .replace(/[\u0000-\u001f\u007f-\u009f/\\:*?"<>|]/g, '_')
    .replace(/\.{2,}/g, '.')
    .replace(/^[\s._]+|[\s.]+$/g, '');
  if (cleaned === '' || /^_+$/.test(cleaned)) return null;
  const chars = Array.from(cleaned);
  if (chars.length <= NAME_LIMIT) return cleaned;
  const dot = cleaned.lastIndexOf('.');
  const extension = dot > 0 ? Array.from(cleaned.slice(dot)) : [];
  if (extension.length > 10) return chars.slice(0, NAME_LIMIT).join('');
  return chars.slice(0, NAME_LIMIT - extension.length).join('') + extension.join('');
}

/**
 * The name a download is saved under: the project's own (RFC 6266
 * `filename*`, which keeps an Arabic name), else the ASCII `filename`,
 * either made safe (`safeFileName`).
 */
export function savedName(disposition: string): string | null {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  if (encoded) {
    try {
      const name = safeFileName(decodeURIComponent(encoded.trim()));
      if (name !== null) return name;
    } catch {
      // A malformed encoding falls back to the plain name.
    }
  }
  const plain = /filename="([^"]+)"/.exec(disposition)?.[1];
  return plain === undefined ? null : safeFileName(plain);
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
    has(payload, 'lines', 'total', 'status', 'findings')
      ? { kind: 'priced', quotation: payload as Quotation }
      : null,
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
  options: Transport & {
    name: string;
    request: ProjectDesignRequest;
    note?: string;
    requirementsInput?: RequirementsInput | null;
  },
): Promise<ProjectOutcome> {
  const { name, request, note = '', requirementsInput } = options;
  return call(
    options,
    PROJECTS,
    requirementsInput
      ? { name, request, note, requirements_input: requirementsInput }
      : { name, request, note },
    saved,
  );
}

/**
 * Save a new revision of a project: `POST /api/v1/design/projects/{id}/revisions`.
 * With a `configuration`, the server composes it into a `pp-design/2` revision,
 * or refuses (`configuration_requires_v2` where this deployment writes v1).
 */
export function reviseProject(
  options: Transport & {
    id: string;
    request: ProjectDesignRequest;
    note?: string;
    configuration?: Record<string, unknown> | null;
    /** M7b: remove the stored configuration (sends `configuration: null`, `configuration_removed: true`). */
    configurationRemoved?: boolean;
    requirementsInput?: RequirementsInput | null;
  },
): Promise<ProjectOutcome> {
  const {
    id,
    request,
    note = '',
    configuration,
    configurationRemoved,
    requirementsInput,
  } = options;
  return call(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}/revisions`,
    {
      request,
      note,
      ...(configurationRemoved
        ? { configuration: null, configuration_removed: true }
        : configuration
          ? { configuration }
          : {}),
      ...(requirementsInput ? { requirements_input: requirementsInput } : {}),
    },
    saved,
  );
}

/**
 * What the guided intake asks the server to record besides the request
 * (`requirements_input`, M6a): how the schedule was entered and each load's
 * control needs. Control needs are requirements only; nothing is designed
 * from them. Boards and loads travel in the request itself.
 */
export type RequirementsInput = {
  intake: 'guided' | 'table' | 'import' | 'ai';
  control: Array<{ board_index: number; load_index: number; needs: ControlNeed[] }>;
  /** With `intake: "ai"` only (M6b): the proposal the save used and where its confirmed items sit. */
  ai?: { proposal_id: string; placements: AiPlacement[] };
};

/** The seven control needs the server records (M6a `ControlNeed`), in its order. */
export const CONTROL_NEEDS = [
  'start_stop',
  'running_indicator',
  'fault_indicator',
  'hand_off_auto',
  'remote_control',
  'interlock',
  'timed',
] as const;
export type ControlNeed = (typeof CONTROL_NEEDS)[number];

export type RequirementSubject = {
  board_ref?: string | null;
  load_ref?: string | null;
  control_ref?: string | null;
  supply_board_ref?: string | null;
  /** An AI proposal item an open item is about (M6b `ai_*` open items). */
  item_id?: string | null;
  /** A (possibly) life-safety mention of the description (M6c), and its term as detection read it. */
  mention_id?: string | null;
  term?: string | null;
};

/** A question the server derived: a value left empty, or an assumption the design made. */
export type RequirementQuestion = {
  id: string;
  kind: 'missing_value' | 'assumption';
  subject: RequirementSubject;
  /** The input it asks for; null for a board-level assumption (`contactor_ac1`, ...). */
  field: string | null;
  finding_code?: string | null;
  /** Whether a value can be given (else it can only be acknowledged), as the server says. */
  answerable?: boolean;
  state: 'open' | 'answered' | 'acknowledged';
  answer?: unknown;
  /** Who answered or acknowledged, as the server recorded it from the token. */
  by_user_id?: string | null;
  /** A display name for `by_user_id`, where the server gives one. */
  by_name?: string | null;
  /** When, by the server's clock (ISO 8601). */
  at?: string | null;
};

/** The versioned requirements record a revision stores (`pp-req/1`). */
export type RequirementsRecord = {
  /** `pp-req/1` today. */
  schema: string;
  /** Derived from an older revision's request: never counts as acknowledged. */
  derived?: boolean;
  intake: string;
  boards: Array<{ ref: string; name: string; fed_from_ref?: string | null }>;
  loads: Array<{ ref: string; board_ref: string; description: string; source?: string }>;
  /** Feeders the server generates from "Fed from" (`<board_ref>.feeder`). */
  feeders: Array<{ ref: string; board_ref: string; supply_board_ref: string; source?: string }>;
  /** Append-only acknowledgements, as stored. */
  acknowledgements: Array<Record<string, string>>;
  control: Array<{
    ref: string;
    board_ref: string;
    load_ref: string;
    needs: string[];
    status: string;
  }>;
  questions: RequirementQuestion[];
  open_items: Array<{ code: string; subject: RequirementSubject }>;
  /** `design_ref` is null where the design was refused. */
  trace: Array<{ requirement_ref: string; design_ref: string | null }>;
  /** M6b: the AI proposal the revision was saved from, its decisions and placements. */
  ai?: AiRecord | null;
};

/** A saved project's requirements record, where the server returned one. */
export function requirementsOf(project: object | null | undefined): RequirementsRecord | null {
  const record = (project as { requirements?: unknown } | null | undefined)?.requirements;
  return typeof record === 'object' && record !== null && 'schema' in record
    ? normalRecord(record as Partial<RequirementsRecord>)
    : null;
}

/** A record with every list present: the API leaves an empty one out. */
function normalRecord(record: Partial<RequirementsRecord>): RequirementsRecord {
  return {
    schema: record.schema ?? 'pp-req/1',
    intake: record.intake ?? 'table',
    derived: record.derived ?? false,
    boards: record.boards ?? [],
    loads: record.loads ?? [],
    feeders: record.feeders ?? [],
    control: record.control ?? [],
    questions: record.questions ?? [],
    open_items: record.open_items ?? [],
    trace: record.trace ?? [],
    acknowledgements: record.acknowledgements ?? [],
    ...(record.ai && typeof record.ai === 'object' ? { ai: record.ai } : {}),
  };
}

export type PreviewOutcome =
  | {
      kind: 'previewed';
      requirements: RequirementsRecord;
      design: BoardDesignResponse | null;
      /** The design's findings, as a revision would store them. */
      findings: DesignFinding[];
    }
  | Failure;

/**
 * The requirements record the server derives from what is entered, with its
 * questions and open items: `POST /api/v1/design/projects/requirements/preview`.
 * Nothing is saved.
 */
export function previewRequirements(
  options: Transport & { request: ProjectDesignRequest; requirementsInput: RequirementsInput },
): Promise<PreviewOutcome> {
  const { request, requirementsInput } = options;
  return call<PreviewOutcome>(
    options,
    `${PROJECTS}/requirements/preview`,
    { request, requirements_input: requirementsInput },
    (payload) => {
      const requirements = requirementsOf(payload);
      if (requirements === null) return null;
      const { design, findings } = payload as { design?: unknown; findings?: unknown };
      return {
        kind: 'previewed',
        requirements,
        findings: Array.isArray(findings) ? (findings as DesignFinding[]) : [],
        design:
          typeof design === 'object' && design !== null ? (design as BoardDesignResponse) : null,
      };
    },
  );
}

/**
 * Answer or acknowledge one question of a saved revision:
 * `POST /api/v1/design/projects/{id}/revisions/{n}/questions/{qid}`. An answer
 * makes a new revision; an acknowledgement is recorded against this one. The
 * server records who and when; the page sends neither.
 */
export type AnswerOutcome = ProjectOutcome | { kind: 'recorded'; requirements: RequirementsRecord };

export function answerQuestion(
  options: Transport & {
    id: string;
    revision: number;
    question: string;
    action: 'answer' | 'acknowledge';
    value?: string;
  },
): Promise<AnswerOutcome> {
  const { id, revision, question, action, value } = options;
  // An answer is a new revision (201, the project); an acknowledgement is
  // recorded on this one (200, its requirements record).
  return call<AnswerOutcome>(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}/revisions/${String(revision)}/questions/${encodeURIComponent(question)}`,
    action === 'answer' ? { action, value } : { action },
    (payload) => {
      const project = saved(payload);
      if (project) return project;
      const requirements = requirementsOf(payload);
      return requirements ? { kind: 'recorded', requirements } : null;
    },
  );
}

/**
 * Resolve one safety concern of the latest revision (M6d):
 * `POST /api/v1/design/projects/{id}/revisions/{n}/requirements/safety-resolutions`,
 * body `{mention_id | item_id, resolution, reason}`. `excluded_from_scope`: the
 * requirement is real and not covered by this revision. `false_positive`: the
 * detected text is not a life-safety requirement. Who and when are the server's.
 */
export function resolveSafetyConcern(
  options: Transport & {
    id: string;
    revision: number;
    concern: Pick<SafetyConcern, 'kind' | 'target_id'> & { proposal_id?: string | null };
    resolution: SafetyResolutionKind;
    reason: string;
    /** M6e: every candidate load confirmed not to be the excluded equipment. */
    notThisEquipment?: LoadRef[];
  },
): Promise<AnswerOutcome> {
  const { id, revision, concern, resolution, reason, notThisEquipment } = options;
  return call<AnswerOutcome>(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}/revisions/${String(revision)}/requirements/safety-resolutions`,
    {
      [concern.kind === 'mention'
        ? 'mention_id'
        : concern.kind === 'item'
          ? 'item_id'
          : 'request_id']: concern.target_id,
      // A carried AI concern names its proposal (M6e); a request one names none.
      ...(concern.kind !== 'request' && concern.proposal_id
        ? { proposal_id: concern.proposal_id }
        : {}),
      resolution,
      reason,
      ...(notThisEquipment ? { not_this_equipment: notThisEquipment } : {}),
    },
    (payload) => {
      const requirements = requirementsOf(payload);
      return requirements ? { kind: 'recorded', requirements } : null;
    },
  );
}

/**
 * Deprecated (M6c): clarify one safety mention of the latest revision as not life-safety:
 * `POST /api/v1/design/projects/{id}/revisions/{n}/requirements/safety-clarifications`.
 * Who and when are the server's; the page sends the mention, the statement and a reason.
 */
export function clarifySafetyMention(
  options: Transport & { id: string; revision: number; mention: string; reason: string },
): Promise<AnswerOutcome> {
  const { id, revision, mention, reason } = options;
  return call<AnswerOutcome>(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}/revisions/${String(revision)}/requirements/safety-clarifications`,
    { mention_id: mention, statement: 'not_life_safety', reason },
    (payload) => {
      const requirements = requirementsOf(payload);
      return requirements ? { kind: 'recorded', requirements } : null;
    },
  );
}

/**
 * Compose a configuration with the project's latest revision, designed now,
 * and return what it would be: `POST /api/v1/design/projects/{id}/configuration/validate`.
 * Nothing is saved.
 */
export function validateConfiguration(
  options: Transport & { id: string; configuration: Record<string, unknown> },
): Promise<ConfigurationOutcome> {
  const { id, configuration } = options;
  return call(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}/configuration/validate`,
    { configuration },
    (payload) =>
      has(payload, 'model', 'findings', 'saveable')
        ? { kind: 'reviewed', review: payload as ConfigurationReview }
        : null,
  );
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

/**
 * Approve one revision, once, as the signed-in account. The request names no
 * one: the server records the account from the token, and names it by its
 * display name. `note` is an optional note to the approval, never an identity.
 */
export function approveRevision(
  options: Transport & { id: string; revision: number; note?: string; scopeConfirmed: true },
): Promise<ProjectOutcome> {
  const { id, revision, note = '', scopeConfirmed } = options;
  return call(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}/revisions/${String(revision)}/approval`,
    // The approver ticked what a review approval covers and does not (M6d).
    { note, scope_confirmed: scopeConfirmed },
    saved,
  );
}

/**
 * Save revision `revision`'s stored request again as the signed-in account's
 * new latest revision, for a different member to approve:
 * `POST …/revisions/{n}/resave`. For a revision whose author the server cannot
 * establish (`author_established: false`). Nothing of the page is sent; a
 * request today's rules refuse comes back refused (e.g. `feeder_server_owned`)
 * and nothing is saved.
 */
export function resaveRevision(
  options: Transport & { id: string; revision: number },
): Promise<ProjectOutcome> {
  const { id, revision } = options;
  return call(
    options,
    `${PROJECTS}/${encodeURIComponent(id)}/revisions/${String(revision)}/resave`,
    {},
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

// ---------------------------------------------------------------------------
// M6b: AI-assisted requirements intake (`docs/architecture/m6b-ai-intake.md`).
// The model proposes; nothing here is designed or validated. Only items the
// designer accepts or edits reach the editor, and only the editor's rows
// reach the request.

/** The most characters a description may have (422 `ai_input_too_long` above it). */
export const AI_DESCRIPTION_LIMIT = 8000;

export const AI_ITEM_KINDS = ['board', 'load', 'control_need', 'question', 'unsupported'] as const;
export type AiItemKind = (typeof AI_ITEM_KINDS)[number];

/** Character offsets into the stored description (Unicode code points, as the server counts). */
export type AiSource = { start: number; end: number };

export type AiItem = {
  id: string;
  kind: AiItemKind;
  source: AiSource;
  /** The described text the item was read from, as the server quoted it. */
  quote: string;
  /** The fields the item proposes; only those its kind may set. */
  value: Record<string, unknown>;
};

/** An item the server refused to keep (bad offsets, a number not in its span, ...). */
export type AiRejectedItem = {
  kind?: string | null;
  source?: AiSource | null;
  quote?: string | null;
  value?: Record<string, unknown> | null;
  reason: string;
};

/** One recorded decision; who and when are the server's. */
export type AiDecision = {
  item_id: string;
  decision: 'accept' | 'reject' | 'edit';
  value?: Record<string, unknown> | null;
  by_user_id?: string | null;
  by_name?: string | null;
  at: string;
};

/** `pp-ai-proposal/1` as the API returns it. */
export type AiProposal = {
  id: string;
  input_text: string;
  provider: string;
  model: string;
  prompt_version?: string;
  created_at?: string;
  created_by_user_id?: string | null;
  items: AiItem[];
  server_rejected: AiRejectedItem[];
  /** Append-only: a later decision on an item supersedes an earlier one. */
  decisions: AiDecision[];
  used_by_revision_id?: string | null;
  proposal_sha256?: string;
  /** The description names fire-pump or life-safety equipment somewhere (never dismissed). */
  life_safety_mentioned?: boolean;
  /** Every life-safety and possibly-safety term keyword detection found (M6c). */
  safety_mentions?: SafetyMention[];
  /** When, and by whom, sending the description to the AI provider was acknowledged. */
  ai_consent_at?: string | null;
  ai_consent_by_user_id?: string | null;
  /** On the response that created it only: the caller's AI calls left this month (null: no cap). */
  calls_remaining?: number | null;
};

/** A term detection found: `life_safety`, or `possibly` (an ambiguous word needing review). */
export type SafetyMention = {
  mention_id: string;
  level: 'life_safety' | 'possibly';
  term: string;
  start: number;
  end: number;
};

/** A mention a second member judged not life-safety: recorded, never engineer-validated. */
export type SafetyClarification = {
  mention_id: string;
  statement: 'not_life_safety';
  reason: string;
  by_user_id: string;
  by_name: string;
  at: string;
};

/** Whether the caller may clarify a mention on the revision open, as the server says. */
export type SafetyClarificationEligibility = { eligible: boolean; reason?: string | null };

/** The server's flag on a saved project, if it sent one. */
export function safetyClarificationOf(
  project: object | null | undefined,
): SafetyClarificationEligibility | null {
  const flag = (project as { safety_clarification?: unknown } | null | undefined)
    ?.safety_clarification;
  if (typeof flag !== 'object' || flag === null) return null;
  const { eligible, reason } = flag as { eligible?: unknown; reason?: unknown };
  return {
    eligible: eligible === true,
    reason: typeof reason === 'string' ? reason : null,
  };
}

/** One load of the request, by its board's name and its position there. */
export type LoadRef = { board: string; load_index: number };

/** The two resolutions of a safety concern (M6d). */
export type SafetyResolutionKind = 'excluded_from_scope' | 'false_positive';
/** One safety concern of the revision open, and how it stands, as the server says. */
export type SafetyConcern = components['schemas']['SafetyConcern'];
/** Why the revision open cannot be approved: unresolved safety concerns. */
export type ApprovalHeld = { count: number; codes: string[] };
/** Whether the caller may resolve safety concerns now, as the server says. */
export type SafetyResolutionEligibility = { eligible: boolean; reason: string | null };

/** The server's safety concerns of a saved project (none when it sent none). */
export function safetyConcernsOf(project: object | null | undefined): SafetyConcern[] {
  const list = (project as { safety_concerns?: unknown } | null | undefined)?.safety_concerns;
  return Array.isArray(list) ? (list as SafetyConcern[]) : [];
}

/** The server's approval hold of a saved project, if it sent one. */
export function approvalHeldOf(project: object | null | undefined): ApprovalHeld | null {
  const held = (project as { approval_held?: unknown } | null | undefined)?.approval_held;
  if (typeof held !== 'object' || held === null) return null;
  const { count, codes } = held as { count?: unknown; codes?: unknown };
  return {
    count: typeof count === 'number' ? count : 0,
    codes: Array.isArray(codes) ? codes.filter((c): c is string => typeof c === 'string') : [],
  };
}

/** The server's flag for the resolve actions, if it sent one. */
export function safetyResolutionOf(
  project: object | null | undefined,
): SafetyResolutionEligibility | null {
  const flag = (project as { safety_resolution?: unknown } | null | undefined)?.safety_resolution;
  if (typeof flag !== 'object' || flag === null) return null;
  const { eligible, reason } = flag as { eligible?: unknown; reason?: unknown };
  return { eligible: eligible === true, reason: typeof reason === 'string' ? reason : null };
}

/** Where a confirmed item sits in the editor when saved. */
export type AiPlacement = { item_id: string; board_index: number; load_index?: number };

/** The `ai` block of a saved revision's requirements record. */
export type AiRecord = {
  proposal: Partial<AiProposal>;
  decisions?: AiDecision[];
  placements?: Array<AiPlacement & { changed_in_editor?: string[] }>;
  /** Append-only clarifications of safety mentions (M6c). */
  clarifications?: SafetyClarification[];
};

export type AiProposalOutcome = { kind: 'proposed'; proposal: AiProposal } | Failure;

/** The status code each AI failure answers with, for a body that carries no code. */
const AI_STATUS_CODES: Readonly<Record<number, string>> = {
  502: 'ai_proposal_malformed',
  503: 'ai_unavailable',
  504: 'ai_timeout',
};

function proposalOf(payload: object): AiProposal | null {
  const value = payload as Partial<AiProposal>;
  if (typeof value.id !== 'string' || !Array.isArray(value.items)) return null;
  if (typeof value.input_text !== 'string') return null;
  return {
    ...value,
    id: value.id,
    input_text: value.input_text,
    provider: typeof value.provider === 'string' ? value.provider : '',
    model: typeof value.model === 'string' ? value.model : '',
    items: value.items,
    server_rejected: Array.isArray(value.server_rejected) ? value.server_rejected : [],
    decisions: Array.isArray(value.decisions) ? value.decisions : [],
  };
}

/** A 502/503/504 the provider answered without a code is still said by its code. */
async function aiCall<T extends { kind: string }>(
  transport: Transport,
  endpoint: string,
  body: unknown,
  accept: (payload: object) => T | null,
  method: 'POST' | 'GET' = 'POST',
): Promise<T | Failure> {
  let status = 0;
  const watched: Transport = {
    ...transport,
    fetchImpl: async (...args: Parameters<typeof fetch>) => {
      const response = await (transport.fetchImpl ?? fetch)(...args);
      status = response.status;
      return response;
    },
  };
  const outcome = await call<T>(
    watched,
    endpoint,
    method === 'GET' ? undefined : body,
    accept,
    undefined,
    method,
    [502, 503, 504],
  );
  const code = AI_STATUS_CODES[status];
  if (code !== undefined && outcome.kind === 'refused' && !(outcome as Refusal).code) {
    return { ...(outcome as Refusal), code };
  }
  if (code !== undefined && outcome.kind === 'failed') {
    return { kind: 'refused', detail: '', code };
  }
  return outcome;
}

const AI_PROPOSALS = `${PROJECTS}/requirements/ai-proposals`;

/**
 * Ask for proposals from a typed description:
 * `POST /api/v1/design/projects/requirements/ai-proposals`, body
 * `{description, request?, ai_consent}`. Only the typed text (and the current
 * draft, so the model can name existing boards) is sent; never a file. The
 * server refuses (`ai_consent_required`) unless `consent` is true: the user
 * acknowledged that the text goes to the AI provider.
 */
export function requestAiProposal(
  options: Transport & {
    description: string;
    request?: ProjectDesignRequest | null;
    consent: boolean;
  },
): Promise<AiProposalOutcome> {
  const { description, request, consent } = options;
  return aiCall<AiProposalOutcome>(
    options,
    AI_PROPOSALS,
    request ? { description, request, ai_consent: consent } : { description, ai_consent: consent },
    (payload) => {
      const proposal = proposalOf(payload);
      return proposal ? { kind: 'proposed', proposal } : null;
    },
  );
}

/**
 * Delete one of the caller's own proposals that no saved revision applied:
 * `DELETE /api/v1/design/projects/requirements/ai-proposals/{id}`.
 */
export function deleteAiProposal(options: Transport & { id: string }): Promise<DeleteOutcome> {
  return call<DeleteOutcome>(
    options,
    `${AI_PROPOSALS}/${encodeURIComponent(options.id)}`,
    undefined,
    () => null,
    (response) => (response.status === 204 ? { kind: 'deleted' } : null),
    'DELETE',
  );
}

/** `GET /api/v1/design/projects/requirements/ai-proposals/{id}`. */
export function getAiProposal(options: Transport & { id: string }): Promise<AiProposalOutcome> {
  return aiCall<AiProposalOutcome>(
    options,
    `${AI_PROPOSALS}/${encodeURIComponent(options.id)}`,
    undefined,
    (payload) => {
      const proposal = proposalOf(payload);
      return proposal ? { kind: 'proposed', proposal } : null;
    },
    'GET',
  );
}

/**
 * Record a decision on one item:
 * `POST …/ai-proposals/{id}/items/{item_id}/decision`, strict body
 * `{"decision": "accept" | "reject"}` or `{"decision": "edit", "value": {...}}`.
 * The server records who and when; the page sends neither. It answers with
 * the proposal as now stored (or, failing that, the decision it recorded).
 */
export function decideAiItem(
  options: Transport & {
    proposal: AiProposal;
    item: string;
    decision: 'accept' | 'reject' | 'edit';
    value?: Record<string, unknown>;
  },
): Promise<AiProposalOutcome> {
  const { proposal, item, decision, value } = options;
  return aiCall<AiProposalOutcome>(
    options,
    `${AI_PROPOSALS}/${encodeURIComponent(proposal.id)}/items/${encodeURIComponent(item)}/decision`,
    decision === 'edit' ? { decision, value } : { decision },
    (payload) => {
      const stored = proposalOf(payload);
      if (stored) return { kind: 'proposed', proposal: stored };
      const recorded = payload as Partial<AiDecision>;
      if (
        recorded.item_id === item &&
        recorded.decision === decision &&
        typeof recorded.at === 'string'
      ) {
        return {
          kind: 'proposed',
          proposal: { ...proposal, decisions: [...proposal.decisions, recorded as AiDecision] },
        };
      }
      return null;
    },
  );
}
