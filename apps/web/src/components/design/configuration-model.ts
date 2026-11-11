/**
 * M7b: the configuration document as the guided editor edits it
 * (docs/architecture/m7b-guided-configuration.md §5; the shape is the
 * composer's, `apps/api/app/design/model/compose.py`).
 *
 * Every function here is pure and returns a new document. Nothing is ever
 * invented: a template is only chosen from definitions already present (the
 * configuration's own `templates`, or the registry definitions an opened
 * revision's model names); a marking or designation is only what the user
 * typed; no rating, symbol or value is added. Keys the editor does not know
 * are carried through untouched.
 */
import type { V2ModelSummary } from '@/lib/design';

export type Configuration = Record<string, unknown>;

/** A configuration designation (`{function, location, product}`) or none. */
export type Designation = {
  function: string | null;
  location: string | null;
  product: string | null;
} | null;

export type ConfigComponent = {
  id: string;
  board: string;
  template: string;
  designation: Designation;
};

export type ConfigStrip = {
  id: string;
  board: string;
  template: string | null;
  designation: Designation;
  /** User-entered points (M7b): `{id, marking, level?, kind?}`. */
  points?: unknown;
};

export type ConfigBinding = {
  id: string;
  pattern: string;
  board: string;
  circuit: string | null;
  markings: Record<string, unknown>;
} & Record<string, unknown>;

/** A template definition the editor may offer, with its review and provenance as stored. */
export type TemplateChoice = {
  ref: string;
  /** The template's function as stored, or null where the source does not say. */
  function: string | null;
  /** The review status as stored; null where none is stated (shown as unknown). */
  reviewStatus: string | null;
  /** Where it came from as stored, or null. */
  provenance: string | null;
  source: 'configuration' | 'registry';
};

export const SECTIONS = ['templates', 'patterns', 'strips', 'components', 'bindings'] as const;

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function list(config: Configuration, key: string): Record<string, unknown>[] {
  const value = config[key];
  if (!Array.isArray(value)) return [];
  return (value as unknown[]).flatMap((item) => {
    const entry = record(item);
    return entry === null ? [] : [entry];
  });
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

/** An empty configuration with every section the composer requires. */
export function emptyConfiguration(): Configuration {
  return { templates: [], patterns: [], strips: [], components: [], bindings: [] };
}

/** The configuration with every required section present (others kept as they are). */
export function normalized(config: Configuration | null | undefined): Configuration {
  const out: Configuration = { ...(config ?? {}) };
  for (const key of SECTIONS) if (!Array.isArray(out[key])) out[key] = [];
  return out;
}

/** Whether a configuration holds nothing the composer would apply. */
export function isEmptyConfiguration(config: Configuration): boolean {
  return SECTIONS.every((key) => list(config, key).length === 0);
}

/** The designation's printed product part (what the editor lets the user type). */
export function designationText(designation: unknown): string {
  const value = record(designation);
  if (value === null) return typeof designation === 'string' ? designation : '';
  return [value.function, value.location, value.product]
    .filter((part): part is string => typeof part === 'string' && part !== '')
    .join(' ');
}

/** A typed designation as the document stores it: the product part only, or none when blank. */
export function designationOf(typed: string, previous?: unknown): Designation {
  const product = typed.trim();
  const old = record(previous);
  if (product === '') {
    return old && (old.function || old.location)
      ? { function: text(old.function), location: text(old.location), product: null }
      : null;
  }
  return {
    function: old ? text(old.function) : null,
    location: old ? text(old.location) : null,
    product,
  };
}

export function componentsOf(config: Configuration): ConfigComponent[] {
  return list(config, 'components') as unknown as ConfigComponent[];
}

export function stripsOf(config: Configuration): ConfigStrip[] {
  return list(config, 'strips') as unknown as ConfigStrip[];
}

export function bindingsOf(config: Configuration): ConfigBinding[] {
  return list(config, 'bindings').map((binding) => ({
    ...binding,
    markings: record(binding.markings) ?? {},
  })) as ConfigBinding[];
}

/**
 * The template definitions available: the configuration's own, then the
 * registry definitions an opened model names that the configuration does not
 * hold. Review status and provenance exactly as stored.
 */
export function templateChoices(
  config: Configuration,
  model: V2ModelSummary | null,
): TemplateChoice[] {
  const choices: TemplateChoice[] = [];
  const seen = new Set<string>();
  for (const template of list(config, 'templates')) {
    const ref = text(template.ref);
    if (ref === null || seen.has(ref)) continue;
    seen.add(ref);
    const provenance = record(template.provenance);
    choices.push({
      ref,
      function: text(template.function),
      reviewStatus: text(record(template.review)?.status),
      provenance: provenance ? text(provenance.source) : null,
      source: 'configuration',
    });
  }
  for (const definition of model?.definitions ?? []) {
    if (definition.kind !== 'template' || seen.has(definition.ref)) continue;
    seen.add(definition.ref);
    choices.push({
      ref: definition.ref,
      function: null,
      reviewStatus: text(definition.review_status),
      provenance: text(definition.provenance_source),
      source: 'registry',
    });
  }
  return choices;
}

/** Whether a review status counts as reviewed; anything else (unknown included) stays flagged. */
export function isReviewed(status: string | null): boolean {
  return status === 'reviewed' || status === 'approved';
}

/**
 * A new, never-reused id: a random token, so an entry added after another was
 * removed never takes the removed one's id (a saved feeder link that named the
 * removed entry's terminal must not silently land on the new one).
 */
function token(): string {
  const bytes = new Uint8Array(6);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function freshId(config: Configuration, prefix: string): string {
  const used = new Set<string>();
  for (const key of ['strips', 'components', 'bindings']) {
    for (const item of list(config, key)) if (typeof item.id === 'string') used.add(item.id);
  }
  let id = `${prefix}-${token()}`;
  while (used.has(id)) id = `${prefix}-${token()}`;
  return id;
}

export function addComponent(
  config: Configuration,
  entry: { board: string; template: string; designation: string },
): Configuration {
  const doc = normalized(config);
  const component: ConfigComponent = {
    id: freshId(doc, 'component'),
    board: entry.board,
    template: entry.template,
    designation: designationOf(entry.designation),
  };
  return { ...doc, components: [...list(doc, 'components'), component] };
}

export function addStrip(
  config: Configuration,
  entry: { board: string; template: string | null; designation: string },
): Configuration {
  const doc = normalized(config);
  const strip: ConfigStrip = {
    id: freshId(doc, 'strip'),
    board: entry.board,
    template: entry.template,
    designation: designationOf(entry.designation),
  };
  return { ...doc, strips: [...list(doc, 'strips'), strip] };
}

export type EntryKind = 'components' | 'strips' | 'bindings';

export function updateEntry(
  config: Configuration,
  kind: EntryKind,
  id: string,
  patch: Record<string, unknown>,
): Configuration {
  const doc = normalized(config);
  return {
    ...doc,
    [kind]: list(doc, kind).map((item) => (item.id === id ? { ...item, ...patch } : item)),
  };
}

export function removeEntry(config: Configuration, kind: EntryKind, id: string): Configuration {
  const doc = normalized(config);
  return { ...doc, [kind]: list(doc, kind).filter((item) => item.id !== id) };
}

/** A pattern point a binding allocates, with the marking the user typed for it ('' when none). */
export type BindingPoint = { id: string; strip: string; kind: string | null; marking: string };

/** The points of a binding's pattern (from the configuration's patterns), with their markings. */
export function bindingPoints(config: Configuration, binding: ConfigBinding): BindingPoint[] {
  const pattern = list(config, 'patterns').find((item) => item.ref === binding.pattern);
  const points = Array.isArray(pattern?.points) ? pattern.points : [];
  const known = points.flatMap((point: unknown) => {
    const value = record(point);
    const id = value ? text(value.id) : null;
    if (value === null || id === null) return [];
    const marking = binding.markings[id];
    return [
      {
        id,
        strip: text(value.strip) ?? '',
        kind: text(value.kind),
        marking: typeof marking === 'string' ? marking : '',
      },
    ];
  });
  // A marking for a point the configuration's patterns do not list (a
  // registry pattern) is still shown and editable, never dropped.
  const listed = new Set(known.map((point) => point.id));
  for (const [id, marking] of Object.entries(binding.markings)) {
    if (!listed.has(id)) {
      known.push({
        id,
        strip: '',
        kind: null,
        marking: typeof marking === 'string' ? marking : '',
      });
    }
  }
  return known;
}

/** A binding with a point's marking set to what was typed, or left unset when blank. */
export function setMarking(
  config: Configuration,
  bindingId: string,
  pointId: string,
  marking: string,
): Configuration {
  const binding = bindingsOf(config).find((item) => item.id === bindingId);
  if (binding === undefined) return config;
  const markings = Object.fromEntries(
    Object.entries(binding.markings).filter(([id]) => id !== pointId),
  );
  if (marking !== '') markings[pointId] = marking;
  return updateEntry(config, 'bindings', bindingId, { markings });
}

/** What a `configuration_conflict` names: the entry to edit or remove. */
export type ConflictTarget = {
  kind: 'binding' | 'strip' | 'component' | 'link';
  /** The configuration entry's id (or the fed board for a link). */
  entry: string;
  ref: string;
  /** The server's stated reference kind (`device`, `circuit`, `board`, `cable`, `terminal`...). */
  refKind: string;
  reason: string;
};

export function conflictTarget(params: Record<string, string> | undefined): ConflictTarget | null {
  if (params === undefined) return null;
  for (const kind of ['binding', 'strip', 'component', 'link'] as const) {
    const entry = params[kind];
    if (typeof entry === 'string' && entry !== '') {
      return {
        kind,
        entry,
        ref: params.ref ?? '',
        refKind: params.kind ?? '',
        reason: params.reason ?? params.refusal ?? '',
      };
    }
  }
  return null;
}

/** The section key of a conflict's entry, or null for a link (edited on its board). */
export function sectionOf(kind: ConflictTarget['kind']): EntryKind | null {
  return kind === 'binding'
    ? 'bindings'
    : kind === 'strip'
      ? 'strips'
      : kind === 'component'
        ? 'components'
        : null;
}

/**
 * A template's name, read from its definition: its stated function and the
 * id part of its ref, with the version (`circuit breaker · synth-mcb-1p (v1)`).
 * Nothing is added that the definition does not hold.
 */
export function templateLabel(ref: string, fn: string | null): string {
  const parts = ref.split('/');
  const version = parts.length >= 3 ? parts[parts.length - 1] : null;
  const id = parts.length >= 3 ? parts.slice(1, -1).join('/') : ref;
  const name = version ? `${id} (${version})` : id;
  return fn ? `${fn.replace(/_/g, ' ')} · ${name}` : name;
}

/** A user-entered strip point (`strips[].points[]`): the marking is the user's text. */
export type StripPoint = { id: string; marking: string; level?: number; kind?: string };

export function stripPoints(strip: { points?: unknown }): StripPoint[] {
  if (!Array.isArray(strip.points)) return [];
  return (strip.points as unknown[]).flatMap((item) => {
    const point = record(item);
    const id = point ? text(point.id) : null;
    if (point === null || id === null) return [];
    return [{ ...point, id, marking: text(point.marking) ?? '' }];
  });
}

/** A strip with a point added: its id is new within the strip and stays as given. */
export function addStripPoint(
  config: Configuration,
  stripId: string,
  entry: { marking: string; level: number | null },
): Configuration {
  const strip = stripsOf(config).find((item) => item.id === stripId);
  if (strip === undefined) return config;
  const points = stripPoints(strip);
  const used = new Set(points.map((point) => point.id));
  let id = `p-${token()}`;
  while (used.has(id)) id = `p-${token()}`;
  const point: StripPoint = {
    id,
    marking: entry.marking,
    ...(entry.level !== null ? { level: entry.level } : {}),
  };
  return updateEntry(config, 'strips', stripId, { points: [...points, point] });
}

/** A strip with one point's marking or level changed (a blank level is left out). */
export function updateStripPoint(
  config: Configuration,
  stripId: string,
  pointId: string,
  patch: { marking?: string; level?: number | null },
): Configuration {
  const strip = stripsOf(config).find((item) => item.id === stripId);
  if (strip === undefined) return config;
  const points = stripPoints(strip).map((point) => {
    if (point.id !== pointId) return point;
    const next: StripPoint = {
      ...point,
      ...(patch.marking !== undefined ? { marking: patch.marking } : {}),
    };
    if (patch.level === null) delete next.level;
    else if (patch.level !== undefined) next.level = patch.level;
    return next;
  });
  return updateEntry(config, 'strips', stripId, { points });
}

export function removeStripPoint(
  config: Configuration,
  stripId: string,
  pointId: string,
): Configuration {
  const strip = stripsOf(config).find((item) => item.id === stripId);
  if (strip === undefined) return config;
  return updateEntry(config, 'strips', stripId, {
    points: stripPoints(strip).filter((point) => point.id !== pointId),
  });
}

/** How a conflict names its entry: the entry's typed designation, else its id. */
export function entryName(
  config: Configuration,
  kind: ConflictTarget['kind'],
  entry: string,
): string {
  const section = sectionOf(kind);
  if (section === null) return entry;
  const found = list(config, section).find((item) => item.id === entry);
  const text = found ? designationText(found.designation) : '';
  return text !== '' ? text : entry;
}

/** A designation as the drawing prints it (`=function+location-product`), or null. */
export function printedDesignation(designation: unknown): string | null {
  const value = record(designation);
  if (value === null || typeof value.product !== 'string' || value.product === '') return null;
  const fn = typeof value.function === 'string' && value.function !== '' ? value.function : null;
  const loc = typeof value.location === 'string' && value.location !== '' ? value.location : null;
  return (fn ? `=${fn}` : '') + (loc ? `+${loc}` : '') + `-${value.product}`;
}

/** A printed form read on its own board: no function aspect means the board's (as the server reads it). */
function onBoard(printed: string, board: string): string {
  return printed.startsWith('=') ? printed : `=${board}${printed}`;
}

/**
 * The configuration entries a `designation_duplicate` finding names: those on
 * its board whose printed designation is one of the forms it lists
 * (`-Q1 / =MB-1-Q1`). A derived part (an incomer) is not an entry, so a
 * finding may match one entry or none.
 */
export function duplicateEntries(
  config: Configuration,
  board: string,
  forms: string,
): Array<{ kind: 'component' | 'strip'; id: string }> {
  const wanted = new Set(
    forms
      .split(' / ')
      .map((form) => form.trim())
      .filter((form) => form !== '')
      .map((form) => onBoard(form, board)),
  );
  const out: Array<{ kind: 'component' | 'strip'; id: string }> = [];
  const check = (
    kind: 'component' | 'strip',
    entry: { id: string; board: string; designation: unknown },
  ) => {
    const printed = printedDesignation(entry.designation);
    if (entry.board === board && printed !== null && wanted.has(onBoard(printed, board))) {
      out.push({ kind, id: entry.id });
    }
  };
  for (const entry of componentsOf(config)) check('component', entry);
  for (const entry of stripsOf(config)) check('strip', entry);
  return out;
}
