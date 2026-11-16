/**
 * M9a: connection patterns the user enters in the guided editor, kept in the
 * same configuration document (`configuration.patterns`, the composer's
 * pattern shape; docs/architecture/m9a-guided-patterns.md).
 *
 * Every function here is pure and returns a new document; a refused change
 * returns the document unchanged. Nothing is inferred: no connection, `via`,
 * core, rating, symbol or rule is added; a point's kind stays `unspecified`
 * unless the user picks a kind already present in the project's patterns; a
 * slot's `device_kind` is kept as stored and never set here; and every pattern
 * entered here is `unreviewed`. Connections already stored (an imported
 * pattern's) are kept exactly; when a member is removed they are re-indexed so
 * each still joins the same two members, and a member a connection names
 * cannot be removed.
 */
import type { Configuration } from '@/components/design/configuration-model';
import {
  DEFINITION_ID,
  definitionRef,
  definitionsOf,
  ENTERED_BY,
  refParts,
  UNSPECIFIED,
} from '@/components/design/definition-model';

/** A slot, role, point or net id inside a pattern (the composer takes any text; this is stricter). */
export const PART_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$/;

export const NET_SCOPES = ['binding', 'board'] as const;
export type NetScope = (typeof NET_SCOPES)[number];
export const SIDES = ['inner', 'outer'] as const;
export type Side = (typeof SIDES)[number];

export type PatternSlot = { id: string; template: string | null; deviceKind: string | null };
export type PatternPoint = {
  id: string;
  strip: string;
  offset: number;
  level: number;
  kind: string;
};
export type PatternMember = { slot: string; terminal: string } | { point: string; side: Side };
export type PatternConnection = {
  from: number;
  to: number;
  via: string;
  core: number | null;
  label: string | null;
};

/** The connection kinds the composer accepts (entities.VIAS); nothing else is offered. */
export const VIAS = ['wire', 'cable_core', 'jumper', 'busbar', 'internal'] as const;
export type Via = (typeof VIAS)[number];
export type PatternNet = {
  role: string;
  signal: string;
  scope: string;
  members: PatternMember[];
  connections: PatternConnection[];
};
export type Pattern = {
  ref: string;
  slots: PatternSlot[];
  strips: string[];
  points: PatternPoint[];
  nets: PatternNet[];
  source: string | null;
  reviewStatus: string;
};

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function items(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.map(record).filter((item): item is Record<string, unknown> => item !== null)
    : [];
}

function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? (value as unknown[]) : [];
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

function int(value: unknown): number {
  return typeof value === 'number' && Number.isInteger(value) ? value : 0;
}

export function isSlotMember(member: PatternMember): member is { slot: string; terminal: string } {
  return 'slot' in member;
}

function memberOf(value: unknown): PatternMember | null {
  const raw = record(value);
  if (raw === null) return null;
  if (typeof raw.slot === 'string') {
    return { slot: raw.slot, terminal: typeof raw.terminal === 'string' ? raw.terminal : '' };
  }
  if (typeof raw.point === 'string') {
    return { point: raw.point, side: raw.side === 'outer' ? 'outer' : 'inner' };
  }
  return null;
}

function netOf(raw: Record<string, unknown>): PatternNet {
  return {
    role: typeof raw.role === 'string' ? raw.role : '',
    signal: typeof raw.signal === 'string' ? raw.signal : '',
    scope: typeof raw.scope === 'string' ? raw.scope : 'binding',
    members: arr(raw.members)
      .map(memberOf)
      .filter((member): member is PatternMember => member !== null),
    connections: items(raw.connections).map((c) => ({
      from: int(c.from),
      to: int(c.to),
      via: typeof c.via === 'string' ? c.via : '',
      core: typeof c.core === 'number' && Number.isInteger(c.core) ? c.core : null,
      label: typeof c.label === 'string' ? c.label : null,
    })),
  };
}

/**
 * The configuration's own patterns that the editor manages: `tenant/` refs.
 * A loaded `global/` pattern is kept as it is.
 */
export function patternsOf(config: Configuration): Pattern[] {
  return items(config.patterns).flatMap((raw) => {
    const ref = text(raw.ref);
    if (ref === null || refParts(ref) === null) return [];
    return [
      {
        ref,
        slots: items(raw.slots).flatMap((slot) => {
          const id = text(slot.id);
          return id === null
            ? []
            : [{ id, template: text(slot.template), deviceKind: text(slot.device_kind) }];
        }),
        strips: arr(raw.strips).filter((role): role is string => typeof role === 'string'),
        points: items(raw.points).flatMap((point) => {
          const id = text(point.id);
          return id === null
            ? []
            : [
                {
                  id,
                  strip: typeof point.strip === 'string' ? point.strip : '',
                  offset: int(point.offset),
                  level: int(point.level),
                  kind: text(point.kind) ?? UNSPECIFIED,
                },
              ];
        }),
        nets: items(raw.nets).map(netOf),
        source: text(record(raw.provenance)?.source),
        reviewStatus: text(record(raw.review)?.status) ?? 'unreviewed',
      },
    ];
  });
}

/** The point kinds already present in the project's patterns, plus `unspecified`. */
export function pointKindChoices(config: Configuration): string[] {
  const seen = new Set<string>([UNSPECIFIED]);
  for (const pattern of items(config.patterns)) {
    for (const point of items(pattern.points)) {
      const kind = text(point.kind);
      if (kind !== null) seen.add(kind);
    }
  }
  return [...seen];
}

/** Why an id typed for a new or renamed pattern cannot be used, or null. */
export function patternIdProblem(
  config: Configuration,
  id: string,
  except: string | null = null,
): 'empty' | 'invalid' | 'taken' | null {
  const typed = id.trim();
  if (typed === '') return 'empty';
  if (!DEFINITION_ID.test(typed)) return 'invalid';
  const taken = items(config.patterns).some((pattern) => {
    const ref = text(pattern.ref);
    if (ref === null || ref === except) return false;
    return refParts(ref)?.id === typed;
  });
  return taken ? 'taken' : null;
}

/** Why an id typed for a slot, role, point or net cannot be used, or null. */
export function partIdProblem(
  existing: readonly string[],
  id: string,
): 'empty' | 'invalid' | 'taken' | null {
  const typed = id.trim();
  if (typed === '') return 'empty';
  if (!PART_ID.test(typed)) return 'invalid';
  return existing.includes(typed) ? 'taken' : null;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** A new pattern: nothing in it yet, nothing inferred, user-entered and unreviewed. */
export function addPattern(config: Configuration, id: string): Configuration {
  if (patternIdProblem(config, id) !== null) return config;
  const pattern = {
    ref: definitionRef(id.trim()),
    slots: [],
    strips: [],
    points: [],
    nets: [],
    provenance: { source: null, author: ENTERED_BY, created: today() },
    review: {
      status: 'unreviewed',
      reviewer: null,
      reviewed_at: null,
      standards: [],
      scope_note: null,
    },
  };
  return { ...config, patterns: [...items(config.patterns), pattern] };
}

function mapPattern(
  config: Configuration,
  ref: string,
  change: (pattern: Record<string, unknown>) => Record<string, unknown>,
): Configuration {
  return {
    ...config,
    patterns: items(config.patterns).map((pattern) =>
      pattern.ref === ref ? change(pattern) : pattern,
    ),
  };
}

/** A pattern with its source document changed (a blank source is none). */
export function updatePatternSource(
  config: Configuration,
  ref: string,
  source: string,
): Configuration {
  return mapPattern(config, ref, (pattern) => ({
    ...pattern,
    provenance: {
      ...(record(pattern.provenance) ?? {}),
      source: source.trim() === '' ? null : source,
    },
  }));
}

/** The bindings that apply a pattern, by id. */
export function patternUsers(config: Configuration, ref: string): string[] {
  return items(config.bindings)
    .filter((binding) => binding.pattern === ref && typeof binding.id === 'string')
    .map((binding) => binding.id as string);
}

/** A pattern renamed, and every binding that applies it pointed at the new ref (version kept). */
export function renamePattern(config: Configuration, ref: string, id: string): Configuration {
  const parts = refParts(ref);
  if (parts === null || patternIdProblem(config, id, ref) !== null) return config;
  const next = definitionRef(id.trim(), parts.version);
  if (next === ref) return config;
  return {
    ...mapPattern(config, ref, (pattern) => ({ ...pattern, ref: next })),
    bindings: items(config.bindings).map((binding) =>
      binding.pattern === ref ? { ...binding, pattern: next } : binding,
    ),
  };
}

/** A pattern removed; refused (unchanged) while a binding applies it. */
export function removePattern(config: Configuration, ref: string): Configuration {
  if (patternUsers(config, ref).length > 0) return config;
  return { ...config, patterns: items(config.patterns).filter((pattern) => pattern.ref !== ref) };
}

/* ---------------------------------------------------------------- slots --- */

function bindingsOf(config: Configuration, ref: string): Record<string, unknown>[] {
  return items(config.bindings).filter((binding) => binding.pattern === ref);
}

function mappedKeys(binding: Record<string, unknown>, key: string): string[] {
  return Object.keys(record(binding[key]) ?? {});
}

/** What names a slot: the nets whose members are on it, and the bindings that fill it. */
export function slotUses(
  config: Configuration,
  ref: string,
  slotId: string,
): { nets: string[]; bindings: string[] } {
  const pattern = patternsOf(config).find((item) => item.ref === ref);
  return {
    nets: (pattern?.nets ?? [])
      .filter((net) => net.members.some((m) => isSlotMember(m) && m.slot === slotId))
      .map((net) => net.role),
    bindings: bindingsOf(config, ref)
      .filter((binding) => mappedKeys(binding, 'slots').includes(slotId))
      .map((binding) => String(binding.id)),
  };
}

export function addSlot(
  config: Configuration,
  ref: string,
  entry: { id: string; template: string | null },
): Configuration {
  const pattern = patternsOf(config).find((item) => item.ref === ref);
  if (pattern === undefined) return config;
  const ids = pattern.slots.map((slot) => slot.id);
  if (partIdProblem(ids, entry.id) !== null) return config;
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    slots: [
      ...items(raw.slots),
      { id: entry.id.trim(), template: entry.template, device_kind: null },
    ],
  }));
}

/** A slot's template changed (null: the bound component's own template). Its device kind is kept. */
export function updateSlotTemplate(
  config: Configuration,
  ref: string,
  slotId: string,
  template: string | null,
): Configuration {
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    slots: items(raw.slots).map((slot) => (slot.id === slotId ? { ...slot, template } : slot)),
  }));
}

export function removeSlot(config: Configuration, ref: string, slotId: string): Configuration {
  const uses = slotUses(config, ref, slotId);
  if (uses.nets.length > 0 || uses.bindings.length > 0) return config;
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    slots: items(raw.slots).filter((slot) => slot.id !== slotId),
  }));
}

/* ----------------------------------------------------------- strip roles --- */

/** What names a strip role: the points on it, and the bindings that map it to a strip. */
export function roleUses(
  config: Configuration,
  ref: string,
  role: string,
): { points: string[]; bindings: string[] } {
  const pattern = patternsOf(config).find((item) => item.ref === ref);
  return {
    points: (pattern?.points ?? []).filter((point) => point.strip === role).map((p) => p.id),
    bindings: bindingsOf(config, ref)
      .filter((binding) => mappedKeys(binding, 'strips').includes(role))
      .map((binding) => String(binding.id)),
  };
}

export function addRole(config: Configuration, ref: string, role: string): Configuration {
  const pattern = patternsOf(config).find((item) => item.ref === ref);
  if (pattern === undefined || partIdProblem(pattern.strips, role) !== null) return config;
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    strips: [...arr(raw.strips), role.trim()],
  }));
}

export function removeRole(config: Configuration, ref: string, role: string): Configuration {
  const uses = roleUses(config, ref, role);
  if (uses.points.length > 0 || uses.bindings.length > 0) return config;
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    strips: arr(raw.strips).filter((item) => item !== role),
  }));
}

/* ---------------------------------------------------------------- points --- */

/** What names a point: the nets whose members are on it, and the bindings that mark it. */
export function pointUses(
  config: Configuration,
  ref: string,
  pointId: string,
): { nets: string[]; bindings: string[] } {
  const pattern = patternsOf(config).find((item) => item.ref === ref);
  return {
    nets: (pattern?.nets ?? [])
      .filter((net) => net.members.some((m) => !isSlotMember(m) && m.point === pointId))
      .map((net) => net.role),
    bindings: bindingsOf(config, ref)
      .filter((binding) => mappedKeys(binding, 'markings').includes(pointId))
      .map((binding) => String(binding.id)),
  };
}

/** Why an offset or level typed for a point cannot be used, or null (a whole number ≥ 1). */
export function positionProblem(typed: string): 'invalid' | null {
  return /^[1-9][0-9]{0,5}$/.test(typed.trim()) ? null : 'invalid';
}

export function addPoint(
  config: Configuration,
  ref: string,
  entry: { id: string; strip: string; offset: number; level: number; kind: string },
): Configuration {
  const pattern = patternsOf(config).find((item) => item.ref === ref);
  if (pattern === undefined) return config;
  if (
    partIdProblem(
      pattern.points.map((point) => point.id),
      entry.id,
    ) !== null
  )
    return config;
  if (!pattern.strips.includes(entry.strip) || entry.offset < 1 || entry.level < 1) return config;
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    points: [
      ...items(raw.points),
      {
        id: entry.id.trim(),
        strip: entry.strip,
        offset: entry.offset,
        level: entry.level,
        kind: entry.kind === '' ? UNSPECIFIED : entry.kind,
      },
    ],
  }));
}

export function updatePoint(
  config: Configuration,
  ref: string,
  pointId: string,
  patch: Partial<{ strip: string; offset: number; level: number; kind: string }>,
): Configuration {
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    points: items(raw.points).map((point) =>
      point.id === pointId ? { ...point, ...patch } : point,
    ),
  }));
}

export function removePoint(config: Configuration, ref: string, pointId: string): Configuration {
  const uses = pointUses(config, ref, pointId);
  if (uses.nets.length > 0 || uses.bindings.length > 0) return config;
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    points: items(raw.points).filter((point) => point.id !== pointId),
  }));
}

/* ------------------------------------------------------------------ nets --- */

function mapNet(
  config: Configuration,
  ref: string,
  role: string,
  change: (net: Record<string, unknown>) => Record<string, unknown>,
): Configuration {
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    nets: items(raw.nets).map((net) => (net.role === role ? change(net) : net)),
  }));
}

/** A new net: no members and no connections yet. */
export function addNet(
  config: Configuration,
  ref: string,
  entry: { role: string; signal: string; scope: NetScope },
): Configuration {
  const pattern = patternsOf(config).find((item) => item.ref === ref);
  if (pattern === undefined) return config;
  if (
    partIdProblem(
      pattern.nets.map((net) => net.role),
      entry.role,
    ) !== null
  )
    return config;
  if (entry.signal.trim() === '') return config;
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    nets: [
      ...items(raw.nets),
      {
        role: entry.role.trim(),
        signal: entry.signal.trim(),
        scope: entry.scope,
        members: [],
        connections: [],
      },
    ],
  }));
}

export function updateNet(
  config: Configuration,
  ref: string,
  role: string,
  patch: Partial<{ signal: string; scope: NetScope }>,
): Configuration {
  return mapNet(config, ref, role, (net) => ({ ...net, ...patch }));
}

/**
 * A net removed, with its stored connections (the caller says how many first).
 * Refused (unchanged) while a binding holds wire details for one of them.
 */
export function removeNet(config: Configuration, ref: string, role: string): Configuration {
  if (wireDetailUses(config, ref, role).length > 0) return config;
  return mapPattern(config, ref, (raw) => ({
    ...raw,
    nets: items(raw.nets).filter((net) => net.role !== role),
  }));
}

function sameMember(a: PatternMember, b: PatternMember): boolean {
  return isSlotMember(a) && isSlotMember(b)
    ? a.slot === b.slot && a.terminal === b.terminal
    : !isSlotMember(a) && !isSlotMember(b) && a.point === b.point && a.side === b.side;
}

/** A member added at the end (connections name members by index, so earlier ones keep theirs). */
export function addMember(
  config: Configuration,
  ref: string,
  role: string,
  member: PatternMember,
): Configuration {
  const net = patternsOf(config)
    .find((item) => item.ref === ref)
    ?.nets.find((item) => item.role === role);
  if (net === undefined || net.members.some((existing) => sameMember(existing, member))) {
    return config;
  }
  if (isSlotMember(member) && member.terminal.trim() === '') return config;
  const stored = isSlotMember(member)
    ? { slot: member.slot, terminal: member.terminal.trim() }
    : { point: member.point, side: member.side };
  return mapNet(config, ref, role, (raw) => ({
    ...raw,
    members: [...arr(raw.members), stored],
  }));
}

/** The stored connections of a net that name a member, by index. */
export function memberConnections(net: PatternNet, index: number): number[] {
  return net.connections
    .map((connection, at) => (connection.from === index || connection.to === index ? at : -1))
    .filter((at) => at >= 0);
}

/**
 * A member removed; refused (unchanged) while a stored connection names it.
 * The net's other connections are re-indexed so each still joins the same two members.
 */
export function removeMember(
  config: Configuration,
  ref: string,
  role: string,
  index: number,
): Configuration {
  const net = patternsOf(config)
    .find((item) => item.ref === ref)
    ?.nets.find((item) => item.role === role);
  if (net === undefined || index < 0 || index >= net.members.length) return config;
  if (memberConnections(net, index).length > 0) return config;
  const shift = (at: unknown) => (typeof at === 'number' && at > index ? at - 1 : at);
  return mapNet(config, ref, role, (raw) => ({
    ...raw,
    members: arr(raw.members).filter((_, at) => at !== index),
    connections: items(raw.connections).map((connection) => ({
      ...connection,
      from: shift(connection.from),
      to: shift(connection.to),
    })),
  }));
}

/* -------------------------------------------------------------- checking --- */

/** A slot template's terminals, where the configuration holds the definition; null otherwise. */
export function templateTerminals(
  config: Configuration,
  template: string | null,
): { id: string; marking: string }[] | null {
  if (template === null) return null;
  const own = definitionsOf(config).find((definition) => definition.ref === template);
  if (own !== undefined) {
    return own.terminals.map((terminal) => ({ id: terminal.id, marking: terminal.marking }));
  }
  const raw = items(config.templates).find((item) => item.ref === template);
  if (raw === undefined) return null;
  return items(raw.terminals).flatMap((terminal) =>
    typeof terminal.id === 'string'
      ? [{ id: terminal.id, marking: typeof terminal.marking === 'string' ? terminal.marking : '' }]
      : [],
  );
}

export type MemberProblem = 'no_slot' | 'no_point' | 'not_on_template' | 'two_nets';

export type PatternProblems = {
  /** By slot id: its template is not one the configuration or registry offers. */
  slots: Record<string, 'unknown_template'>;
  /** By point id: its strip role is not one of the pattern's. */
  points: Record<string, 'no_role'>;
  /** By net role. */
  nets: Record<string, 'no_members' | 'no_signal'>;
  /** By `${role}#${index}`. */
  members: Record<string, MemberProblem>;
  /** By `${role}#${index}`: what the pattern check would refuse in a stored connection. */
  connections: Record<string, ConnectionProblem>;
};

/**
 * What the composer would refuse in a pattern, found before saving (the server
 * still decides). `knownTemplates` are the refs the editor offers.
 */
export function patternProblems(
  config: Configuration,
  pattern: Pattern,
  knownTemplates: ReadonlySet<string>,
): PatternProblems {
  const problems: PatternProblems = {
    slots: {},
    points: {},
    nets: {},
    members: {},
    connections: {},
  };
  const slots = new Map(pattern.slots.map((slot) => [slot.id, slot]));
  const points = new Set(pattern.points.map((point) => point.id));
  for (const slot of pattern.slots) {
    if (slot.template !== null && !knownTemplates.has(slot.template)) {
      problems.slots[slot.id] = 'unknown_template';
    }
  }
  for (const point of pattern.points) {
    if (!pattern.strips.includes(point.strip)) problems.points[point.id] = 'no_role';
  }
  // The net a binding would make: per binding by role, per board by signal.
  const owner = new Map<string, string>();
  for (const net of pattern.nets) {
    net.connections.forEach((connection, index) => {
      const problem = connectionProblem(net, connection);
      if (problem !== null) problems.connections[`${net.role}#${String(index)}`] = problem;
    });
    if (net.members.length === 0) problems.nets[net.role] = 'no_members';
    else if (net.signal.trim() === '') problems.nets[net.role] = 'no_signal';
    const same = net.scope === 'board' ? `board|${net.signal}` : `binding|${net.role}`;
    const seen = new Set<string>();
    net.members.forEach((member, index) => {
      const key = `${net.role}#${String(index)}`;
      let id: string;
      if (isSlotMember(member)) {
        const slot = slots.get(member.slot);
        id = `slot|${member.slot}|${member.terminal}`;
        if (slot === undefined) {
          problems.members[key] = 'no_slot';
          return;
        }
        const terminals = templateTerminals(config, slot.template);
        if (terminals !== null && !terminals.some((terminal) => terminal.id === member.terminal)) {
          problems.members[key] = 'not_on_template';
          return;
        }
      } else {
        id = `point|${member.point}|${member.side}`;
        if (!points.has(member.point)) {
          problems.members[key] = 'no_point';
          return;
        }
      }
      // A repeat within one net joins nothing new; the composer accepts it (as before M9a).
      if (seen.has(id)) return;
      seen.add(id);
      const first = owner.get(id);
      if (first === undefined) owner.set(id, same);
      else if (first !== same) problems.members[key] = 'two_nets';
    });
  }
  return problems;
}

export function problemCount(problems: PatternProblems): number {
  return (
    Object.keys(problems.slots).length +
    Object.keys(problems.points).length +
    Object.keys(problems.nets).length +
    Object.keys(problems.members).length +
    Object.keys(problems.connections).length
  );
}

/* ----------------------------------------------------------- connections --- */

/**
 * Per-wire details a binding holds for a connection of this net: the binding
 * id and the key (`role/index`, the connection's position in the net; the
 * composer derives the wire's and connection's ids from it). With `from`,
 * only keys at that index or after it.
 */
export function wireDetailUses(
  config: Configuration,
  ref: string,
  role: string,
  from = 0,
): { binding: string; key: string; index: number }[] {
  const out: { binding: string; key: string; index: number }[] = [];
  const prefix = `${role}/`;
  for (const binding of items(config.bindings)) {
    if (binding.pattern !== ref || typeof binding.id !== 'string') continue;
    for (const key of Object.keys(record(binding.wires) ?? {})) {
      if (!key.startsWith(prefix)) continue;
      const tail = key.slice(prefix.length);
      // A key that is not a plain index names no connection; it is kept and counted as a use.
      const index = /^\d+$/.test(tail) ? Number(tail) : Number.POSITIVE_INFINITY;
      if (index >= from) out.push({ binding: binding.id, key, index });
    }
  }
  return out;
}

/** A connection as the editor writes it: core and label only for a cable core. */
export type ConnectionDraft = {
  from: number;
  to: number;
  via: Via;
  core: number | null;
  label: string | null;
};

function stored(draft: ConnectionDraft): Record<string, unknown> {
  const cable = draft.via === 'cable_core';
  const label =
    cable && draft.label !== null && draft.label.trim() !== '' ? draft.label.trim() : null;
  return {
    from: draft.from,
    to: draft.to,
    via: draft.via,
    core: cable ? draft.core : null,
    label,
  };
}

export type ConnectionProblem =
  | 'end_missing'
  | 'same_end'
  | 'unknown_via'
  | 'internal_not_sides'
  | 'core_missing'
  | 'core_invalid'
  | 'core_not_allowed'
  | 'label_not_allowed';

/** What the pattern check (codec_v2.parse_pattern) would refuse in a connection; null if nothing. */
export function connectionProblem(
  net: PatternNet,
  c: { from: number; to: number; via: string; core: number | null; label: string | null },
): ConnectionProblem | null {
  const n = net.members.length;
  if (!(c.from >= 0 && c.from < n && c.to >= 0 && c.to < n)) return 'end_missing';
  if (c.from === c.to) return 'same_end';
  if (!(VIAS as readonly string[]).includes(c.via)) return 'unknown_via';
  if (c.via === 'cable_core') {
    if (c.core === null) return 'core_missing';
    if (!Number.isInteger(c.core) || c.core < 1) return 'core_invalid';
  } else {
    if (c.core !== null) return 'core_not_allowed';
    if (c.label !== null) return 'label_not_allowed';
  }
  if (c.via === 'internal') {
    const a = net.members[c.from];
    const b = net.members[c.to];
    if (
      a === undefined ||
      b === undefined ||
      isSlotMember(a) ||
      isSlotMember(b) ||
      a.point !== b.point
    ) {
      return 'internal_not_sides';
    }
  }
  return null;
}

function netIn(config: Configuration, ref: string, role: string): PatternNet | undefined {
  return patternsOf(config)
    .find((item) => item.ref === ref)
    ?.nets.find((item) => item.role === role);
}

/** A connection added at the end (earlier connections, and wire details keyed to them, keep their index). */
export function addConnection(
  config: Configuration,
  ref: string,
  role: string,
  draft: ConnectionDraft,
): Configuration {
  const net = netIn(config, ref, role);
  if (net === undefined) return config;
  const entry = stored(draft);
  if (connectionProblem(net, entry as PatternConnection) !== null) return config;
  return mapNet(config, ref, role, (raw) => ({
    ...raw,
    connections: [...arr(raw.connections), entry],
  }));
}

/**
 * A connection changed in place, as the user edits it (a problem is said, not
 * refused). Refused (unchanged) while a binding holds wire details for it:
 * they were entered for that connection as it was.
 */
export function updateConnection(
  config: Configuration,
  ref: string,
  role: string,
  index: number,
  draft: ConnectionDraft,
): Configuration {
  const net = netIn(config, ref, role);
  if (net === undefined || index < 0 || index >= net.connections.length) return config;
  if (wireDetailUses(config, ref, role).some((use) => use.index === index)) return config;
  // Ends are members of this net and the kind is one the composer knows; what else the
  // check would refuse (a missing core, sides of two points) is said beside the fields.
  const n = net.members.length;
  if (!(draft.from >= 0 && draft.from < n && draft.to >= 0 && draft.to < n)) return config;
  if (!(VIAS as readonly string[]).includes(draft.via)) return config;
  const entry = stored(draft);
  return mapNet(config, ref, role, (raw) => ({
    ...raw,
    connections: arr(raw.connections).map((connection, at) =>
      at === index ? { ...(record(connection) ?? {}), ...entry } : connection,
    ),
  }));
}

/**
 * A connection removed. Wire details are keyed by position, so removing one
 * would drop its details and move every later connection's details onto
 * another connection: refused (unchanged) while a binding holds wire details
 * at this index or after it.
 */
export function removeConnection(
  config: Configuration,
  ref: string,
  role: string,
  index: number,
): Configuration {
  const net = netIn(config, ref, role);
  if (net === undefined || index < 0 || index >= net.connections.length) return config;
  if (wireDetailUses(config, ref, role, index).length > 0) return config;
  return mapNet(config, ref, role, (raw) => ({
    ...raw,
    connections: arr(raw.connections).filter((_, at) => at !== index),
  }));
}
