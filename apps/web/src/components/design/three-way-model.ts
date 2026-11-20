/**
 * M19: a read-only three-way review of unsaved work.
 *
 * Three sources: the base (the revision the work was made from), your unsaved
 * work (a captured copy of the editor's inputs) and the latest saved revision
 * (as fetched). All three are compared in one representation: the base and
 * latest stored requests are passed through the editor's own conversion
 * (`fromRequest` then `toRequest`), the same one your work comes from, so a
 * stored default the form fills in is never a "change". What that conversion
 * does not carry is not compared; where base and latest differ in it, it is
 * listed as changed outside this comparison, never as unchanged.
 *
 * Identity is #236's (never position, never a generated id):
 * - boards by name; loads by description within a board, unique on each
 *   side (a description held by several loads on any side is a duplicate
 *   group: unresolved unless identical on all three sides);
 * - feeder links by fed board; templates and patterns by ref; strips,
 *   components and bindings by board and id;
 * - binding wire details (a position in a pattern net) only while the net and
 *   everything its positions connect are the same on all three sides;
 *   otherwise that role is unresolved.
 * Nothing is recalculated; stored results are not compared (your unsaved work
 * has none). A one-sided change is never declared safe to combine: changes it
 * depends on from the other side are listed, without a verdict.
 */
import { fromRequest, toRequest } from '@/components/design/schedule';
import {
  canonical,
  configurationOf,
  endpointsOf,
  flatten,
  netOf,
  same,
  unitOf,
  valuesOf,
  type Raw,
  type Scalar,
} from '@/components/design/revision-compare-model';
import type { SavedProject } from '@/lib/design';

/** One source of the review, in the editor's representation. */
export type Source = {
  request: Raw;
  /** `unavailable`: this source holds no configuration that can be compared. */
  configuration: Raw | 'unavailable';
  /** Whether feeder links can be held (pp-design/3). */
  links: boolean;
};

export type Status = 'yours' | 'latest' | 'same' | 'both' | 'conflict' | 'unresolved';
export type FieldStatus = 'yours' | 'latest' | 'same' | 'conflict';
export type Field = {
  field: string;
  base?: Scalar;
  yours?: Scalar;
  latest?: Scalar;
  status: FieldStatus;
  unit?: string;
};
export type Presence = { base: boolean; yours: boolean; latest: boolean };
export type Row = {
  key: string;
  section: SectionId;
  type: string;
  label: string;
  where?: string;
  status: Status;
  presence: Presence;
  fields: Field[];
  /** For unresolved rows: why, and each side's entries as they are. */
  reason?: 'duplicate' | 'wire_endpoints';
  sides?: {
    base: Record<string, Scalar>[];
    yours: Record<string, Scalar>[];
    latest: Record<string, Scalar>[];
  };
  /** Changes on the other side this row refers to (no compatibility is claimed). */
  dependsOn: { label: string; type: string; side: 'yours' | 'latest' }[];
};
export type SectionId = 'details' | 'boards' | 'links' | 'configuration';
export type Review = {
  sections: {
    id: SectionId;
    status: 'compared' | 'unavailable';
    unavailableIn: ('base' | 'yours' | 'latest')[];
    rows: Row[];
  }[];
  /** Stored fields changed between base and latest that this comparison does not carry. */
  outside: string[];
};

type Entry = {
  key: string;
  section: SectionId;
  type: string;
  label: string;
  where?: string;
  value: Raw | null;
  /** A key held by several entries on this side (no identity within it). */
  group?: Raw[];
  /** Keys of entries this one refers to. */
  refs: string[];
};

type Wires = { label: string; where: string; details: Raw; context: string | null };

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

const SEP = '\u0000';

/** A stored revision in the editor's representation (as a form would send it). */
export function storedSource(project: SavedProject): Source {
  const form = fromRequest(project.request, 0);
  return {
    request: toRequest(form.info, form.boards, null),
    configuration: configurationOf(project),
    links: project.snapshot_schema === 'pp-design/3',
  };
}

function without(value: Raw, keys: string[]): Raw {
  return Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key)));
}

function entriesOf(source: Source): { entries: Map<string, Entry>; wires: Map<string, Wires> } {
  const list: Entry[] = [];
  const info = record(source.request.info) ?? {};
  list.push({
    key: 'details',
    section: 'details',
    type: 'details',
    label: 'project',
    value: info,
    refs: [],
  });
  for (const board of records(source.request.boards)) {
    const name = str(board.name) ?? '';
    const fed = str(board.fed_from);
    list.push({
      key: `board:${name}`,
      section: 'boards',
      type: 'board',
      label: name,
      value: without(board, ['loads', 'name']),
      refs: fed ? [`board:${fed}`] : [],
    });
    for (const load of records(board.loads)) {
      const description = str(load.description) ?? '';
      list.push({
        key: `load:${name}${SEP}${description}`,
        section: 'boards',
        type: 'load',
        label: description,
        where: name,
        value: without(load, ['description']),
        refs: [`board:${name}`],
      });
    }
  }
  if (source.links) {
    for (const link of records(source.request.feeder_links)) {
      const fed = str(link.fed_board) ?? '';
      list.push({
        key: `link:${fed}`,
        section: 'links',
        type: 'link',
        label: fed,
        value: without(link, ['fed_board']),
        refs: [`board:${fed}`],
      });
    }
  }
  const wires = new Map<string, Wires>();
  const config = source.configuration;
  if (config !== 'unavailable') {
    for (const [type, key] of [
      ['template', 'templates'],
      ['pattern', 'patterns'],
    ] as const) {
      for (const item of records(config[key])) {
        const ref = str(item.ref) ?? '';
        list.push({
          key: `${type}:${ref}`,
          section: 'configuration',
          type,
          label: ref,
          value: without(item, ['ref']),
          refs: [],
        });
      }
    }
    for (const [type, key] of [
      ['strip', 'strips'],
      ['component', 'components'],
    ] as const) {
      for (const item of records(config[key])) {
        const id = str(item.id) ?? '';
        const board = str(item.board) ?? '';
        const template = str(item.template);
        list.push({
          key: `${type}:${board}${SEP}${id}`,
          section: 'configuration',
          type,
          label: id,
          where: board,
          value: without(item, ['id']),
          refs: template ? [`template:${template}`] : [],
        });
      }
    }
    for (const binding of records(config.bindings)) {
      const id = str(binding.id) ?? '';
      const board = str(binding.board) ?? '';
      const refs = [`pattern:${str(binding.pattern) ?? ''}`];
      for (const slot of Object.values(record(binding.slots) ?? {})) {
        const component = str(record(slot)?.component);
        if (component) refs.push(`component:${board}${SEP}${component}`);
      }
      for (const strip of Object.values(record(binding.strips) ?? {})) {
        if (typeof strip === 'string') refs.push(`strip:${board}${SEP}${strip}`);
      }
      list.push({
        key: `binding:${board}${SEP}${id}`,
        section: 'configuration',
        type: 'binding',
        label: id,
        where: board,
        value: without(binding, ['id', 'wires']),
        refs,
      });
      const byRole = new Map<string, Raw>();
      for (const [wireKey, detail] of Object.entries(record(binding.wires) ?? {})) {
        const role = wireKey.split('/')[0] ?? wireKey;
        byRole.set(role, { ...(byRole.get(role) ?? {}), [wireKey]: detail });
      }
      for (const [role, details] of byRole) {
        const net = netOf(config, binding.pattern, role);
        const ends = endpointsOf(config, binding, role);
        wires.set(`${board}${SEP}${id}${SEP}${role}`, {
          label: role,
          where: id,
          details,
          context:
            net === null || ends === null
              ? null
              : canonical({ net, ends, pattern: binding.pattern }),
        });
      }
    }
  }
  const entries = new Map<string, Entry>();
  for (const entry of list) {
    const held = entries.get(entry.key);
    if (held === undefined) entries.set(entry.key, entry);
    else
      entries.set(entry.key, {
        ...held,
        value: null,
        group: [...(held.group ?? (held.value ? [held.value] : [])), entry.value ?? {}],
      });
  }
  return { entries, wires };
}

/** Field-level differences of two values (decimal quantities compared digit by digit). */
function differs(a: Raw | null | undefined, b: Raw | null | undefined): boolean {
  if ((a === null || a === undefined) !== (b === null || b === undefined)) return true;
  const x = flatten(a ?? {});
  const y = flatten(b ?? {});
  for (const key of new Set([...x.keys(), ...y.keys()]))
    if (!same(x.get(key), y.get(key), key)) return true;
  return false;
}

function fieldsOf(b: Raw | null, y: Raw | null, l: Raw | null): Field[] {
  const fb = flatten(b ?? {});
  const fy = flatten(y ?? {});
  const fl = flatten(l ?? {});
  const keys = [...new Set([...fb.keys(), ...fy.keys(), ...fl.keys()])].sort();
  return keys.flatMap((field) => {
    const vb = fb.get(field) ?? null;
    const vy = y === null ? null : (fy.get(field) ?? null);
    const vl = l === null ? null : (fl.get(field) ?? null);
    const yc = !same(vb, vy, field);
    const lc = !same(vb, vl, field);
    if (!yc && !lc) return [];
    const status: FieldStatus =
      yc && lc ? (same(vy, vl, field) ? 'same' : 'conflict') : yc ? 'yours' : 'latest';
    const unit = unitOf(field);
    return [
      {
        field,
        ...(vb === null ? {} : { base: vb }),
        ...(vy === null ? {} : { yours: vy }),
        ...(vl === null ? {} : { latest: vl }),
        status,
        ...(unit ? { unit } : {}),
      },
    ];
  });
}

function sorted(group: Raw[]): string {
  return group
    .map((item) => canonical(item))
    .sort()
    .join('\n');
}

function classify(key: string, b?: Entry, y?: Entry, l?: Entry): Row | null {
  const any = (b ?? y ?? l) as Entry;
  const presence = { base: b !== undefined, yours: y !== undefined, latest: l !== undefined };
  const base = {
    key,
    section: any.section,
    type: any.type,
    label: any.label,
    ...(any.where ? { where: any.where } : {}),
    presence,
    dependsOn: [],
  };
  if ([b, y, l].some((e) => e?.group !== undefined)) {
    const v = (e?: Entry) =>
      e === undefined ? '' : e.group ? `G${sorted(e.group)}` : `V${canonical(e.value)}`;
    if (v(b) === v(y) && v(b) === v(l)) return null;
    const side = (e?: Entry) =>
      e === undefined ? [] : (e.group ?? [e.value ?? {}]).map((item) => valuesOf(item));
    return {
      ...base,
      status: 'unresolved',
      reason: 'duplicate',
      fields: [],
      sides: { base: side(b), yours: side(y), latest: side(l) },
    };
  }
  const vb = b?.value ?? null;
  const vy = y?.value ?? null;
  const vl = l?.value ?? null;
  const yc =
    presence.yours !== presence.base || (presence.yours && presence.base && differs(vb, vy));
  const lc =
    presence.latest !== presence.base || (presence.latest && presence.base && differs(vb, vl));
  if (!yc && !lc) return null;
  const fields = fieldsOf(vb, vy, vl);
  let status: Status;
  if (yc && !lc) status = 'yours';
  else if (lc && !yc) status = 'latest';
  else if (presence.yours === presence.latest && !differs(vy, vl)) status = 'same';
  // Removed on one side, changed or kept on the other: a conflict.
  else if (presence.yours !== presence.latest) status = 'conflict';
  else status = fields.some((f) => f.status === 'conflict') ? 'conflict' : 'both';
  return { ...base, status, fields };
}

/** Classify base, yours and latest. */
export function threeWay(base: Source, yours: Source, latest: Source): Review {
  const eb = entriesOf(base);
  const ey = entriesOf(yours);
  const el = entriesOf(latest);
  const rows: Row[] = [];
  const keys = [
    ...new Set([...eb.entries.keys(), ...ey.entries.keys(), ...el.entries.keys()]),
  ].sort();
  for (const key of keys) {
    const row = classify(key, eb.entries.get(key), ey.entries.get(key), el.entries.get(key));
    if (row) rows.push(row);
  }
  // Wire details: by position only while net and endpoints are one on all three sides.
  const wireKeys = [
    ...new Set([...eb.wires.keys(), ...ey.wires.keys(), ...el.wires.keys()]),
  ].sort();
  for (const key of wireKeys) {
    const wb = eb.wires.get(key);
    const wy = ey.wires.get(key);
    const wl = el.wires.get(key);
    const any = (wb ?? wy ?? wl) as Wires;
    const [board = ''] = key.split(SEP);
    const ctx = [wb, wy, wl].map((w) => (w === undefined ? undefined : w.context));
    const sameEnds = ctx.every((c) => c !== null && c !== undefined && c === ctx[0]);
    if (!sameEnds) {
      const d = [wb, wy, wl].map((w) => canonical(w?.details ?? {}));
      if (d[0] === d[1] && d[0] === d[2]) continue;
      const side = (w?: Wires) =>
        Object.entries(w?.details ?? {}).map(([wire, detail]) => ({ wire, ...valuesOf(detail) }));
      rows.push({
        key: `wires:${key}`,
        section: 'configuration',
        type: 'wires',
        label: any.label,
        where: any.where,
        status: 'unresolved',
        reason: 'wire_endpoints',
        presence: { base: wb !== undefined, yours: wy !== undefined, latest: wl !== undefined },
        fields: [],
        sides: { base: side(wb), yours: side(wy), latest: side(wl) },
        dependsOn: [],
      });
      continue;
    }
    const wireIds = [
      ...new Set([
        ...Object.keys(wb?.details ?? {}),
        ...Object.keys(wy?.details ?? {}),
        ...Object.keys(wl?.details ?? {}),
      ]),
    ].sort();
    for (const wire of wireIds) {
      const mk = (w?: Wires): Entry | undefined => {
        const detail = record(w?.details[wire]);
        return detail
          ? {
              key: wire,
              section: 'configuration',
              type: 'wire',
              label: wire,
              where: any.where,
              value: detail,
              refs: [],
            }
          : undefined;
      };
      const row = classify(`wire:${key}${SEP}${wire}`, mk(wb), mk(wy), mk(wl));
      if (row) rows.push({ ...row, where: `${any.where} · ${board}` });
    }
  }
  // Dependencies: a one-sided row refers to something changed on the other side.
  const byKey = new Map(rows.map((row) => [row.key, row]));
  const refsOf = (key: string) =>
    (ey.entries.get(key) ?? eb.entries.get(key) ?? el.entries.get(key))?.refs ?? [];
  const latestRefs = (key: string) => el.entries.get(key)?.refs ?? eb.entries.get(key)?.refs ?? [];
  for (const row of rows) {
    if (row.status !== 'yours' && row.status !== 'latest') continue;
    const refs = row.status === 'yours' ? refsOf(row.key) : latestRefs(row.key);
    for (const ref of refs) {
      const other = byKey.get(ref);
      if (!other) continue;
      const touched = row.status === 'yours' ? other.status !== 'yours' : other.status !== 'latest';
      if (touched)
        row.dependsOn.push({
          label: other.label,
          type: other.type,
          side: row.status === 'yours' ? 'latest' : 'yours',
        });
    }
  }
  const unavailableConfig = (
    [
      ['base', base],
      ['yours', yours],
      ['latest', latest],
    ] as const
  )
    .filter(([, s]) => s.configuration === 'unavailable')
    .map(([n]) => n);
  const unavailableLinks = (
    [
      ['base', base],
      ['yours', yours],
      ['latest', latest],
    ] as const
  )
    .filter(([, s]) => !s.links)
    .map(([n]) => n);
  const section = (id: SectionId, unavailableIn: ('base' | 'yours' | 'latest')[]) => ({
    id,
    status: unavailableIn.length > 0 ? ('unavailable' as const) : ('compared' as const),
    unavailableIn,
    rows: unavailableIn.length > 0 ? [] : rows.filter((row) => row.section === id),
  });
  return {
    sections: [
      section('details', []),
      section('boards', []),
      section('links', unavailableLinks),
      section('configuration', unavailableConfig),
    ],
    outside: [],
  };
}

/**
 * Stored fields that changed between base and latest but are not carried by
 * the editor's representation (and so are not compared): as paths, for the
 * review to list as changed outside it. Boards and loads are paired by the
 * same identity rules (unique name, unique description); title-block entries
 * (each save records its own) are not listed.
 */
export function outsideChanges(
  base: SavedProject,
  latest: SavedProject,
  carried: Source,
  carriedLatest: Source,
): string[] {
  const out = new Set<string>();
  const kept = (value: Raw) => new Set(flatten(value).keys());
  const unique = (list: Raw[], key: (item: Raw) => string | null) => {
    const counts = new Map<string, Raw[]>();
    for (const item of list) {
      const k = key(item);
      if (k !== null) counts.set(k, [...(counts.get(k) ?? []), item]);
    }
    return new Map(
      [...counts]
        .filter(([, items]) => items.length === 1)
        .map(([k, items]) => [k, items[0] as Raw]),
    );
  };
  const diff = (a: Raw, b: Raw, keep: Set<string>, prefix: string, skip: string[] = []) => {
    const fa = flatten(a);
    const fb = flatten(b);
    for (const key of new Set([...fa.keys(), ...fb.keys()])) {
      if (skip.some((s) => key === s || key.startsWith(`${s}.`))) continue;
      if (keep.has(key)) continue;
      if (!same(fa.get(key), fb.get(key), key)) out.add(`${prefix}${key}`);
    }
  };
  const rb = base.request as unknown as Raw;
  const rl = latest.request as unknown as Raw;
  diff(
    record(rb.info) ?? {},
    record(rl.info) ?? {},
    kept(record(carried.request.info) ?? {}),
    'info.',
    ['revisions'],
  );
  if (canonical(rb.profile ?? null) !== canonical(rl.profile ?? null)) out.add('profile');
  const boardsB = unique(records(rb.boards), (b) => str(b.name));
  const boardsL = unique(records(rl.boards), (b) => str(b.name));
  const carriedBoards = unique(records(carriedLatest.request.boards), (b) => str(b.name));
  for (const [name, bb] of boardsB) {
    const bl = boardsL.get(name);
    if (!bl) continue;
    const shape = carriedBoards.get(name) ?? {};
    diff(bb, bl, kept(without(shape, ['loads'])), `${name}: `, ['loads', 'name']);
    const loadsB = unique(records(bb.loads), (x) => str(x.description));
    const loadsL = unique(records(bl.loads), (x) => str(x.description));
    const carriedLoads = unique(records(shape.loads), (x) => str(x.description));
    for (const [description, lb] of loadsB) {
      const ll = loadsL.get(description);
      if (ll)
        diff(lb, ll, kept(carriedLoads.get(description) ?? {}), `${name} › ${description}: `, [
          'description',
        ]);
    }
  }
  return [...out].sort();
}
