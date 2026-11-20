/**
 * M18: a read-only comparison of two saved revisions of one project.
 *
 * Pure: it reads two `SavedProject` answers (each already scoped to the
 * caller's tenant and integrity-checked by the server) and says what differs.
 * Nothing is recalculated: entered input is compared as entered, and stored
 * output only as stored (never re-derived under today's rules).
 *
 * Identity rules (never position, never a generated id):
 * - boards: by name (the server refuses two boards with one name);
 * - loads: by description within a board matched by name, only when that
 *   description occurs once in that board on both sides; otherwise, if both
 *   sides hold the very same loads they are "identical", else an ambiguous
 *   group shows both sides;
 * - feeder links: by the fed board's name (once on each side, else ambiguous);
 * - configuration templates and patterns: by their `ref`;
 * - strips, components and bindings: by `id` together with board (editor ids
 *   are random and never reused; the same id on another board is shown as
 *   removed and added, never as a move);
 * - binding wire details: keyed by a position in the pattern's net
 *   (`role/index`), so matched only when that net is unchanged on both sides;
 *   otherwise both sides are listed as ambiguous;
 * - findings: by source, code, severity, message and values (not by their
 *   generated circuit or device ids), as counts.
 * A rename is never inferred: it reads as one removed and one added.
 */
import type { SavedProject } from '@/lib/design';

export type Scalar = string | number | boolean | null;
/** `undefined`: the value is not set on that side. */
export type FieldChange = { field: string; before?: Scalar; after?: Scalar; unit?: string };
export type ItemKind = 'added' | 'removed' | 'changed' | 'unchanged' | 'identical';
export type Ambiguous = {
  label: string;
  reason: 'duplicate' | 'wire_positions';
  before: Record<string, Scalar>[];
  after: Record<string, Scalar>[];
};
export type Item = {
  kind: ItemKind;
  /** What the item is, as entered (a board name, a load description, a ref). */
  label: string;
  /** The section's sub-kind: "board", "load", "template", "binding"... */
  type: string;
  /** On a board or binding: where it is ("board MDB"). */
  where?: string;
  changes: FieldChange[];
  /** For "identical": how many identical entries. For added/removed: their values. */
  count?: number;
  values?: Record<string, Scalar>;
  children?: Item[];
  ambiguous?: Ambiguous[];
};
export type SectionId = 'details' | 'boards' | 'links' | 'configuration' | 'outputs';
export type Section = {
  id: SectionId;
  /** `unavailable`: one or both revisions do not hold this data (never "unchanged"). */
  status: 'compared' | 'unavailable';
  unavailableIn: number[];
  items: Item[];
  ambiguous: Ambiguous[];
};
export type Comparison =
  | { kind: 'compared'; before: number; after: number; sections: Section[] }
  | { kind: 'integrity'; failed: number[] };

/** Findings that mean the stored design is not readable as stored. */
const INTEGRITY = new Set(['snapshot_integrity_failed', 'snapshot_schema_unknown']);

const UNITS: Record<string, string> = {
  power_kw: 'kW',
  length_m: 'm',
  feeder_length_m: 'm',
  voltage_v: 'V',
  fault_level_ka: 'kA',
  frequency_hz: 'Hz',
  earth_loop_ohm: 'Ω',
  ambient_temp_c: '°C',
  insulation_rating_c: '°C',
  cross_section_mm2: 'mm²',
};

type Raw = Record<string, unknown>;

function record(value: unknown): Raw | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Raw)
    : null;
}

function records(value: unknown): Raw[] {
  return Array.isArray(value)
    ? value.flatMap((item: unknown) => {
        const r = record(item);
        return r === null ? [] : [r];
      })
    : [];
}

function str(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

/** Canonical JSON (sorted keys), so equal content compares equal however it was stored. */
export function canonical(value: unknown): string {
  const plain = (item: unknown): unknown =>
    Array.isArray(item)
      ? item.map(plain)
      : record(item) !== null
        ? Object.fromEntries(
            Object.entries(item as Raw)
              .filter(([, inner]) => inner !== undefined)
              .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
              .map(([key, inner]) => [key, plain(inner)]),
          )
        : item;
  return JSON.stringify(plain(value));
}

/** Flatten to path → scalar; lists are kept whole as canonical text (never matched by position). */
export function flatten(value: unknown, prefix = ''): Map<string, Scalar> {
  const out = new Map<string, Scalar>();
  const walk = (item: unknown, path: string) => {
    if (Array.isArray(item)) {
      out.set(path, item.length === 0 ? null : canonical(item));
    } else if (record(item) !== null) {
      for (const [key, inner] of Object.entries(item as Raw)) {
        walk(inner, path === '' ? key : `${path}.${key}`);
      }
    } else if (item === undefined || item === null || item === '') {
      out.set(path, null);
    } else if (typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean') {
      out.set(path, item);
    } else {
      out.set(path, canonical(item));
    }
  };
  walk(value, prefix);
  return out;
}

const DECIMAL = /^-?\d+(\.\d+)?$/;

function same(a: Scalar | undefined, b: Scalar | undefined): boolean {
  const na = a ?? null;
  const nb = b ?? null;
  if (na === nb) return true;
  // "1.50" and "1.5" are one value as stored decimals.
  if (typeof na === 'string' && typeof nb === 'string' && DECIMAL.test(na) && DECIMAL.test(nb)) {
    return Number(na) === Number(nb);
  }
  return false;
}

function unitOf(path: string): string | undefined {
  return UNITS[path.split('.').pop() ?? ''];
}

/** Field-by-field differences of two values of one matched item. */
export function fieldChanges(before: unknown, after: unknown, skip: string[] = []): FieldChange[] {
  const a = flatten(before);
  const b = flatten(after);
  const keys = [...new Set([...a.keys(), ...b.keys()])].filter(
    (key) => !skip.some((s) => key === s || key.startsWith(`${s}.`)),
  );
  keys.sort();
  return keys.flatMap((field) => {
    const x = a.get(field) ?? null;
    const y = b.get(field) ?? null;
    if (same(x, y)) return [];
    const unit = unitOf(field);
    return [
      {
        field,
        ...(x === null ? {} : { before: x }),
        ...(y === null ? {} : { after: y }),
        ...(unit ? { unit } : {}),
      },
    ];
  });
}

function valuesOf(value: unknown, skip: string[] = []): Record<string, Scalar> {
  const out: Record<string, Scalar> = {};
  for (const [key, inner] of flatten(value)) {
    if (skip.some((s) => key === s || key.startsWith(`${s}.`))) continue;
    if (inner !== null) out[key] = inner;
  }
  return out;
}

/**
 * Match two lists by a key, never by position. A key occurring once on each
 * side is a match; a key held by several entries on a side is resolved only
 * when both sides hold the same multiset of entries ("identical"), otherwise it
 * is an ambiguous group. Entries without a key are unmatched (added/removed).
 */
function matchBy(
  type: string,
  before: Raw[],
  after: Raw[],
  key: (item: Raw) => string | null,
  label: (item: Raw) => string,
  compare: (a: Raw, b: Raw) => Item,
  skip: string[] = [],
): { items: Item[]; ambiguous: Ambiguous[] } {
  const group = (list: Raw[]) => {
    const map = new Map<string, Raw[]>();
    const keyless: Raw[] = [];
    for (const item of list) {
      const k = key(item);
      if (k === null) keyless.push(item);
      else map.set(k, [...(map.get(k) ?? []), item]);
    }
    return { map, keyless };
  };
  const a = group(before);
  const b = group(after);
  const items: Item[] = [];
  const ambiguous: Ambiguous[] = [];
  const keys = [...new Set([...a.map.keys(), ...b.map.keys()])].sort();
  for (const k of keys) {
    const left = a.map.get(k) ?? [];
    const right = b.map.get(k) ?? [];
    if (left.length === 1 && right.length === 1) {
      items.push(compare(left[0] as Raw, right[0] as Raw));
    } else if (right.length === 0) {
      for (const item of left)
        items.push({
          kind: 'removed',
          type,
          label: label(item),
          changes: [],
          values: valuesOf(item, skip),
        });
    } else if (left.length === 0) {
      for (const item of right)
        items.push({
          kind: 'added',
          type,
          label: label(item),
          changes: [],
          values: valuesOf(item, skip),
        });
    } else {
      const multiset = (list: Raw[]) =>
        list
          .map((item) => canonical(item))
          .sort()
          .join('\n');
      if (left.length === right.length && multiset(left) === multiset(right)) {
        items.push({ kind: 'identical', type, label: k, changes: [], count: left.length });
      } else {
        ambiguous.push({
          label: k,
          reason: 'duplicate',
          before: left.map((item) => valuesOf(item, skip)),
          after: right.map((item) => valuesOf(item, skip)),
        });
      }
    }
  }
  for (const item of a.keyless)
    items.push({
      kind: 'removed',
      type,
      label: label(item),
      changes: [],
      values: valuesOf(item, skip),
    });
  for (const item of b.keyless)
    items.push({
      kind: 'added',
      type,
      label: label(item),
      changes: [],
      values: valuesOf(item, skip),
    });
  return { items, ambiguous };
}

function leaf(
  type: string,
  label: string,
  a: unknown,
  b: unknown,
  skip: string[] = [],
  where?: string,
): Item {
  const changes = fieldChanges(a, b, skip);
  return {
    kind: changes.length > 0 ? 'changed' : 'unchanged',
    type,
    label,
    changes,
    ...(where ? { where } : {}),
  };
}

function compareDetails(a: SavedProject, b: SavedProject): Section {
  const info = (p: SavedProject) => record(p.request.info) ?? {};
  const items: Item[] = [leaf('details', 'project', info(a), info(b), ['revisions'])];
  const blocks = (p: SavedProject) => records(info(p).revisions);
  // Title-block entries are matched by their whole content: an edited entry is one removed, one added.
  const titled = matchBy(
    'title_revision',
    blocks(a),
    blocks(b),
    (item) => canonical(item),
    (item) => str(item.index) ?? '',
    (x) => ({ kind: 'unchanged', type: 'title_revision', label: str(x.index) ?? '', changes: [] }),
  );
  items.push(...titled.items);
  return {
    id: 'details',
    status: 'compared',
    unavailableIn: [],
    items,
    ambiguous: titled.ambiguous,
  };
}

function compareBoards(a: SavedProject, b: SavedProject): Section {
  const boards = (p: SavedProject) => records(p.request.boards);
  const result = matchBy(
    'board',
    boards(a),
    boards(b),
    (board) => str(board.name),
    (board) => str(board.name) ?? '',
    (x, y) => {
      const name = str(x.name) ?? '';
      const loads = matchBy(
        'load',
        records(x.loads),
        records(y.loads),
        (load) => str(load.description),
        (load) => str(load.description) ?? '',
        (l, r) => leaf('load', str(l.description) ?? '', l, r, ['description'], name),
      );
      const own = fieldChanges(x, y, ['loads', 'name']);
      const changedLoads = loads.items.some(
        (item) => item.kind !== 'unchanged' && item.kind !== 'identical',
      );
      return {
        kind:
          own.length > 0 || changedLoads || loads.ambiguous.length > 0 ? 'changed' : 'unchanged',
        type: 'board',
        label: name,
        changes: own,
        children: loads.items,
        ambiguous: loads.ambiguous,
      };
    },
    ['loads'],
  );
  // Added or removed boards list their loads by description (values only, nothing matched).
  for (const item of result.items) {
    if (item.kind !== 'added' && item.kind !== 'removed') continue;
    const source = (item.kind === 'added' ? boards(b) : boards(a)).find(
      (board) => str(board.name) === item.label,
    );
    item.children = records(source?.loads).map((load) => ({
      kind: item.kind,
      type: 'load',
      label: str(load.description) ?? '',
      where: item.label,
      changes: [],
      values: valuesOf(load, ['description']),
    }));
  }
  return {
    id: 'boards',
    status: 'compared',
    unavailableIn: [],
    items: result.items,
    ambiguous: result.ambiguous,
  };
}

const V3 = 'pp-design/3';

function compareLinks(a: SavedProject, b: SavedProject): Section {
  // Feeder links are entered only under pp-design/3: an older revision holds none to compare.
  const unavailableIn = [a, b].filter((p) => p.snapshot_schema !== V3).map((p) => p.revision);
  if (unavailableIn.length > 0) {
    return { id: 'links', status: 'unavailable', unavailableIn, items: [], ambiguous: [] };
  }
  const links = (p: SavedProject) => records(p.request.feeder_links);
  const result = matchBy(
    'link',
    links(a),
    links(b),
    (link) => str(link.fed_board),
    (link) => str(link.fed_board) ?? '',
    (x, y) => leaf('link', str(x.fed_board) ?? '', x, y, ['fed_board']),
    ['fed_board'],
  );
  return { id: 'links', status: 'compared', unavailableIn: [], ...result };
}

function configurationOf(p: SavedProject): Raw | 'unavailable' {
  if (p.configuration_status === 'not_recoverable') return 'unavailable';
  if (
    p.snapshot_schema === 'pp-design/1' ||
    p.snapshot_schema === null ||
    p.snapshot_schema === undefined
  ) {
    return p.configuration_status === 'stored' && record(p.configuration)
      ? (p.configuration as Raw)
      : 'unavailable';
  }
  return record(p.configuration) ?? {};
}

function netOf(config: Raw, patternRef: unknown, role: string): string | null {
  const pattern = records(config.patterns).find((item) => item.ref === patternRef);
  const net = records(pattern?.nets).find((item) => item.role === role);
  return net ? canonical(net) : null;
}

function compareBinding(ca: Raw, cb: Raw, x: Raw, y: Raw): Item {
  const id = str(x.id) ?? '';
  const where = str(x.board) ?? '';
  const changes = fieldChanges(x, y, ['wires', 'id']);
  const wiresA = record(x.wires) ?? {};
  const wiresB = record(y.wires) ?? {};
  const children: Item[] = [];
  const ambiguous: Ambiguous[] = [];
  const roles = new Set<string>();
  for (const key of [...Object.keys(wiresA), ...Object.keys(wiresB)])
    roles.add(key.split('/')[0] ?? key);
  for (const role of [...roles].sort()) {
    const keysOf = (wires: Raw) =>
      Object.keys(wires).filter((key) => (key.split('/')[0] ?? key) === role);
    const netA = netOf(ca, x.pattern, role);
    const netB = netOf(cb, y.pattern, role);
    const positional = x.pattern === y.pattern && netA !== null && netA === netB;
    if (!positional) {
      const left = keysOf(wiresA);
      const right = keysOf(wiresB);
      // Same details on both sides: nothing to say, whatever the net.
      if (
        canonical(left.map((k) => [k, wiresA[k]])) === canonical(right.map((k) => [k, wiresB[k]]))
      )
        continue;
      ambiguous.push({
        label: role,
        reason: 'wire_positions',
        before: left.map((k) => ({ wire: k, ...valuesOf(wiresA[k]) })),
        after: right.map((k) => ({ wire: k, ...valuesOf(wiresB[k]) })),
      });
      continue;
    }
    for (const key of [...new Set([...keysOf(wiresA), ...keysOf(wiresB)])].sort()) {
      if (!(key in wiresB)) {
        children.push({
          kind: 'removed',
          type: 'wire',
          label: key,
          where: id,
          changes: [],
          values: valuesOf(wiresA[key]),
        });
      } else if (!(key in wiresA)) {
        children.push({
          kind: 'added',
          type: 'wire',
          label: key,
          where: id,
          changes: [],
          values: valuesOf(wiresB[key]),
        });
      } else {
        const item = leaf('wire', key, wiresA[key], wiresB[key], [], id);
        if (item.kind !== 'unchanged') children.push(item);
      }
    }
  }
  const kind =
    changes.length > 0 || children.length > 0 || ambiguous.length > 0 ? 'changed' : 'unchanged';
  return { kind, type: 'binding', label: id, where, changes, children, ambiguous };
}

function compareConfiguration(a: SavedProject, b: SavedProject): Section {
  const ca = configurationOf(a);
  const cb = configurationOf(b);
  const unavailableIn = [
    ...(ca === 'unavailable' ? [a.revision] : []),
    ...(cb === 'unavailable' ? [b.revision] : []),
  ];
  if (ca === 'unavailable' || cb === 'unavailable') {
    return { id: 'configuration', status: 'unavailable', unavailableIn, items: [], ambiguous: [] };
  }
  const items: Item[] = [];
  const ambiguous: Ambiguous[] = [];
  const byRef = (type: string, key: string) => {
    const r = matchBy(
      type,
      records(ca[key]),
      records(cb[key]),
      (item) => str(item.ref),
      (item) => str(item.ref) ?? '',
      (x, y) => leaf(type, str(x.ref) ?? '', x, y, ['ref']),
      ['ref'],
    );
    items.push(...r.items);
    ambiguous.push(...r.ambiguous);
  };
  byRef('template', 'templates');
  byRef('pattern', 'patterns');
  const byId = (type: string, key: string, compare?: (x: Raw, y: Raw) => Item) => {
    const r = matchBy(
      type,
      records(ca[key]),
      records(cb[key]),
      // Id together with board: the same id on another board is not claimed as a move.
      (item) =>
        str(item.id) === null ? null : `${str(item.board) ?? ''}\u0000${str(item.id) ?? ''}`,
      (item) => str(item.id) ?? '',
      compare ?? ((x, y) => leaf(type, str(x.id) ?? '', x, y, ['id'], str(x.board) ?? undefined)),
      ['id'],
    );
    for (const item of r.items)
      if (item.where === undefined && item.values?.board !== undefined)
        item.where = String(item.values.board);
    items.push(...r.items);
    ambiguous.push(
      ...r.ambiguous.map((group) => ({
        ...group,
        label: group.label.split('\u0000').reverse().join(' · '),
      })),
    );
  };
  byId('strip', 'strips');
  byId('component', 'components');
  byId('binding', 'bindings', (x, y) => compareBinding(ca, cb, x, y));
  return { id: 'configuration', status: 'compared', unavailableIn: [], items, ambiguous };
}

function findingKey(finding: Raw): string {
  return canonical({
    source: finding.source ?? null,
    code: finding.code ?? null,
    severity: finding.severity ?? null,
    message: finding.message ?? null,
    values: finding.values ?? null,
  });
}

function compareOutputs(a: SavedProject, b: SavedProject): Section {
  const items: Item[] = [];
  const hashA = a.snapshot_sha256 ?? null;
  const hashB = b.snapshot_sha256 ?? null;
  const unavailableIn = [
    ...(hashA === null ? [a.revision] : []),
    ...(hashB === null ? [b.revision] : []),
  ];
  items.push(
    leaf(
      'output',
      'design',
      {
        validated: a.validated,
        rules_version: a.rules_version ?? null,
        snapshot_schema: a.snapshot_schema ?? null,
      },
      {
        validated: b.validated,
        rules_version: b.rules_version ?? null,
        snapshot_schema: b.snapshot_schema ?? null,
      },
    ),
  );
  // The stored design itself is compared only as same or different (its SHA-256):
  // its parts carry generated ids, so nothing inside it is matched.
  if (hashA !== null && hashB !== null) {
    items.push({
      kind: hashA === hashB ? 'unchanged' : 'changed',
      type: 'output',
      label: 'stored_design',
      changes: [],
    });
  }
  // Findings as stored, counted by what they say (never by their generated ids).
  const count = (p: SavedProject) => {
    const map = new Map<string, { finding: Raw; n: number }>();
    for (const finding of records(p.findings)) {
      const key = findingKey(finding);
      const entry = map.get(key);
      map.set(key, { finding, n: (entry?.n ?? 0) + 1 });
    }
    return map;
  };
  const fa = count(a);
  const fb = count(b);
  for (const key of [...new Set([...fa.keys(), ...fb.keys()])].sort()) {
    const left = fa.get(key);
    const right = fb.get(key);
    const finding = (left ?? right)?.finding ?? {};
    const label = `${str(finding.code) ?? ''}: ${str(finding.message) ?? ''}`;
    const values = { severity: (str(finding.severity) ?? null) as Scalar };
    if (left && right) {
      if (left.n !== right.n) {
        items.push({
          kind: 'changed',
          type: 'finding',
          label,
          changes: [{ field: 'count', before: left.n, after: right.n }],
        });
      }
    } else {
      items.push({
        kind: left ? 'removed' : 'added',
        type: 'finding',
        label,
        changes: [],
        values,
        count: (left ?? right)?.n ?? 1,
      });
    }
  }
  return {
    id: 'outputs',
    status: unavailableIn.length === 2 ? 'unavailable' : 'compared',
    unavailableIn,
    items,
    ambiguous: [],
  };
}

/** Compare revision `before` with revision `after` (both as the server answered them). */
export function compareRevisions(before: SavedProject, after: SavedProject): Comparison {
  const failed = [before, after]
    .filter((p) => records(p.findings).some((finding) => INTEGRITY.has(str(finding.code) ?? '')))
    .map((p) => p.revision);
  if (failed.length > 0) return { kind: 'integrity', failed };
  return {
    kind: 'compared',
    before: before.revision,
    after: after.revision,
    sections: [
      compareDetails(before, after),
      compareBoards(before, after),
      compareLinks(before, after),
      compareConfiguration(before, after),
      compareOutputs(before, after),
    ],
  };
}

/** How many entries of a section differ (identical and unchanged ones are not counted). */
export function changedCount(section: Section): number {
  return (
    section.items.filter((item) => item.kind !== 'unchanged' && item.kind !== 'identical').length +
    section.ambiguous.length
  );
}
