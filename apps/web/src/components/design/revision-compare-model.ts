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
export type ItemKind = 'added' | 'removed' | 'changed' | 'unchanged' | 'identical' | 'unavailable';
export type Ambiguous = {
  label: string;
  reason: 'duplicate' | 'wire_positions' | 'wire_endpoints';
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

/** Fields that hold a decimal quantity (so "1.50" and "1.5" are one value); labels never are. */
const QUANTITIES = new Set([...Object.keys(UNITS), 'power_factor']);

/** A decimal written canonically, digit by digit (no float rounding): "01.50" → "1.5". */
function decimalText(value: string): string {
  const negative = value.startsWith('-');
  const [whole = '0', fraction = ''] = (negative ? value.slice(1) : value).split('.');
  const w = whole.replace(/^0+(?=\d)/, '');
  const f = fraction.replace(/0+$/, '');
  const text = f === '' ? w : `${w}.${f}`;
  return negative && text !== '0' ? `-${text}` : text;
}

function same(a: Scalar | undefined, b: Scalar | undefined, field: string): boolean {
  const na = a ?? null;
  const nb = b ?? null;
  if (na === nb) return true;
  // Only a quantity is normalised: a wire or job "number" of "01" is not "1".
  const last =
    field
      .split('.')
      .filter((part) => part !== 'value')
      .pop() ?? '';
  if (
    QUANTITIES.has(last) &&
    typeof na === 'string' &&
    typeof nb === 'string' &&
    DECIMAL.test(na) &&
    DECIMAL.test(nb)
  ) {
    return decimalText(na) === decimalText(nb);
  }
  return false;
}

export function unitOf(path: string): string | undefined {
  const parts = path.split('.');
  // A wire detail stores `{value, source}`: its unit is its field's.
  const last =
    parts[parts.length - 1] === 'value' ? parts[parts.length - 2] : parts[parts.length - 1];
  return UNITS[last ?? ''];
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
    if (same(x, y, field)) return [];
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
  items.push(
    ...titled.items.sort((x, y) => x.label.localeCompare(y.label, 'en', { numeric: true })),
  );
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
        ['description'],
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

/** A stored value as text (scalars as they are, anything else as canonical JSON). */
function txt(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : canonical(value);
}

/** A pattern net's members and connections as a reader sees them (positions are 1-based). */
function readableNet(net: Raw): Raw {
  const members = records(net.members).map((member, index) =>
    member.slot !== undefined
      ? `${String(index + 1)}. ${txt(member.slot)}.${txt(member.terminal)}`
      : `${String(index + 1)}. ${txt(member.point)} (${txt(member.side)})`,
  );
  const connections = records(net.connections).map((c) =>
    [
      `${String(Number(c.from) + 1)}→${String(Number(c.to) + 1)}`,
      txt(c.via),
      c.core === null || c.core === undefined ? null : `core ${txt(c.core)}`,
      typeof c.label === 'string' && c.label !== '' ? `“${c.label}”` : null,
    ]
      .filter((part) => part !== null)
      .join(' '),
  );
  return {
    signal: net.signal ?? null,
    scope: net.scope ?? null,
    members: members.length === 0 ? null : members.join('; '),
    connections: connections.length === 0 ? null : connections.join('; '),
  };
}

/** A pattern: its own fields, then slots and points by id and nets by role (never by position). */
function comparePattern(x: Raw, y: Raw): Item {
  const ref = str(x.ref) ?? '';
  const changes = fieldChanges(x, y, ['ref', 'slots', 'points', 'nets']);
  const children: Item[] = [];
  const ambiguous: Ambiguous[] = [];
  const part = (type: string, key: string, shape: (item: Raw) => Raw) => {
    const r = matchBy(
      type,
      records(x[key]).map(shape),
      records(y[key]).map(shape),
      (item) => str(item.key),
      (item) => str(item.key) ?? '',
      (l, rr) => leaf(type, str(l.key) ?? '', l, rr, ['key'], ref),
      ['key'],
    );
    for (const item of r.items)
      if (item.kind !== 'unchanged') children.push({ ...item, where: ref });
    ambiguous.push(...r.ambiguous);
  };
  const keyed = (field: string) => (item: Raw) => {
    const { [field]: key, ...rest } = item;
    return { key: key ?? null, ...rest };
  };
  part('slot', 'slots', keyed('id'));
  part('point', 'points', keyed('id'));
  part('net', 'nets', (item) => ({ key: item.role ?? null, ...readableNet(item) }));
  const kind =
    changes.length > 0 || children.length > 0 || ambiguous.length > 0 ? 'changed' : 'unchanged';
  return { kind, type: 'pattern', label: ref, changes, children, ambiguous };
}

function netOf(config: Raw, patternRef: unknown, role: string): string | null {
  const pattern = records(config.patterns).filter((item) => item.ref === patternRef);
  // One pattern and one net with that role, or no positional match at all.
  if (pattern.length !== 1) return null;
  const nets = records(pattern[0]?.nets).filter((item) => item.role === role);
  return nets.length === 1 ? canonical(nets[0]) : null;
}

/**
 * What a binding's net positions for `role` connect, as stored: for each net
 * member in order, the slot's mapping (component, device or other) with that
 * component's stored definition and its template's stored definition (its
 * terminals), or the point with its strip (the strip role's mapping and that
 * strip's stored definition, with its points and markings) and the binding's
 * marking for it; and the binding's cable when the net uses a cable core.
 * Nothing is resolved further or guessed: null when the net is not unique.
 */
function endpointsOf(config: Raw, binding: Raw, role: string): string | null {
  const patterns = records(config.patterns).filter((item) => item.ref === binding.pattern);
  if (patterns.length !== 1) return null;
  const pattern = patterns[0] as Raw;
  const nets = records(pattern.nets).filter((item) => item.role === role);
  if (nets.length !== 1) return null;
  const net = nets[0] as Raw;
  const board = binding.board;
  const slots = record(binding.slots) ?? {};
  const strips = record(binding.strips) ?? {};
  const markings = record(binding.markings) ?? {};
  const one = (list: Raw[]) =>
    list.length === 1 ? list[0] : list.length === 0 ? null : 'not_unique';
  const members = records(net.members).map((member) => {
    if (member.slot !== undefined) {
      const mapping = record(slots[str(member.slot) ?? '']) ?? null;
      const componentId = mapping ? str(mapping.component) : null;
      const component =
        componentId === null
          ? null
          : one(
              records(config.components).filter((c) => c.id === componentId && c.board === board),
            );
      const templateRef = record(component) ? (component as Raw).template : null;
      const template =
        typeof templateRef === 'string'
          ? one(records(config.templates).filter((t) => t.ref === templateRef))
          : null;
      return { slot: member.slot, terminal: member.terminal, mapping, component, template };
    }
    const point = one(records(pattern.points).filter((pt) => pt.id === member.point));
    const stripRole = record(point) ? str((point as Raw).strip) : null;
    const stripId = stripRole === null ? null : str(strips[stripRole]);
    const strip =
      stripId === null
        ? null
        : one(records(config.strips).filter((st) => st.id === stripId && st.board === board));
    return {
      point: member.point,
      side: member.side,
      definition: point,
      strip_role: stripRole,
      strip,
      marking: markings[str(member.point) ?? ''] ?? null,
    };
  });
  const cable = records(net.connections).some((c) => c.via === 'cable_core')
    ? (binding.cable ?? null)
    : undefined;
  return canonical({ members, cable });
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
    const sameNet = x.pattern === y.pattern && netA !== null && netA === netB;
    // A wire position names the same physical connection only if what its
    // members resolve to (components, strips, terminal definitions, markings,
    // cable) is unchanged too; a remapped slot or strip is another connection.
    const sameEnds = sameNet && endpointsOf(ca, x, role) === endpointsOf(cb, y, role);
    if (!sameEnds) {
      const left = keysOf(wiresA);
      const right = keysOf(wiresB);
      // No wire details on either side: nothing is claimed either way.
      if (left.length === 0 && right.length === 0) continue;
      // Even equal details are listed: they may now describe another connection.
      ambiguous.push({
        label: role,
        reason: sameNet ? 'wire_endpoints' : 'wire_positions',
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
  const patterns = matchBy(
    'pattern',
    records(ca.patterns),
    records(cb.patterns),
    (item) => str(item.ref),
    (item) => str(item.ref) ?? '',
    comparePattern,
    ['ref'],
  );
  items.push(...patterns.items);
  ambiguous.push(...patterns.ambiguous);
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

// Findings are counted by what kind they are: their messages and values can
// name generated ids (a wire's id), which are not identity across revisions.
function findingKey(finding: Raw): string {
  return canonical({
    source: finding.source ?? null,
    code: finding.code ?? null,
    severity: finding.severity ?? null,
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
  } else if (hashA !== null || hashB !== null) {
    // One side holds no stored design: that is unavailable, never "unchanged".
    items.push({
      kind: 'unavailable',
      type: 'output',
      label: 'stored_design',
      changes: [],
      count: hashA === null ? a.revision : b.revision,
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
    const label = str(finding.code) ?? '';
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
