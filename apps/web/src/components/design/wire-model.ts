/**
 * M9c: per-wire details of a binding, entered in the guided editor and kept in
 * the same configuration document (`bindings[].wires`, the composer's shape;
 * docs/architecture/m9c-guided-wire-details.md).
 *
 * A binding's `wires` maps a key `role/index` (a wire connection's position in
 * its pattern net; the composer derives the wire's id from it) to the details
 * given for that wire: `number`, `cross_section_mm2` and `colour`. A value is
 * text (source `data`) or `{value, source, statement?}` with source `user`,
 * `data` or `assumption` (only an assumption states what it assumes).
 *
 * Every function here is pure and returns a new document; a refused change
 * returns it unchanged. Nothing is inferred or defaulted: a field nobody gave
 * stays unset. A value entered here is stored as `user`, which says who entered
 * it, not that anyone checked it. A value stored with another source is never
 * changed in place: it is replaced only when the user chooses to, and removed
 * only when the user asks. Keys and fields this slice does not know are kept.
 */
import {
  bindingsOf,
  type ConfigBinding,
  type Configuration,
  designationText,
} from '@/components/design/configuration-model';
import { templateTerminals } from '@/components/design/pattern-model';

/** The wire fields the composer accepts (compose._WIRE_FIELDS), in its order. */
export const WIRE_FIELDS = ['number', 'cross_section_mm2', 'colour'] as const;
export type WireField = (typeof WIRE_FIELDS)[number];
export const WIRE_SOURCES = ['user', 'data', 'assumption'] as const;

/** The stored v2 bounds the snapshot reader applies (codec_v2: MAX_TEXT, _POSITIVE_DECIMAL). */
export const MAX_WIRE_TEXT = 200;
const POSITIVE_DECIMAL = /^[0-9]{1,6}(\.[0-9]{1,6})?$/;

type Raw = Record<string, unknown>;

function record(value: unknown): Raw | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Raw)
    : null;
}

function items(value: unknown): Raw[] {
  return Array.isArray(value) ? value.map(record).filter((item): item is Raw => item !== null) : [];
}

/** A wire value as stored, read without changing it. */
export type WireValue =
  | { kind: 'unset' }
  | { kind: 'given'; value: string; source: string; statement: string | null }
  /** Not a shape the composer reads (kept exactly, said as a problem). */
  | { kind: 'unreadable' };

export function wireValue(raw: unknown): WireValue {
  if (raw === undefined || raw === null) return { kind: 'unset' };
  if (typeof raw === 'string')
    return { kind: 'given', value: raw, source: 'data', statement: null };
  const obj = record(raw);
  if (obj === null || typeof obj.value !== 'string' || typeof obj.source !== 'string') {
    return { kind: 'unreadable' };
  }
  return {
    kind: 'given',
    value: obj.value,
    source: obj.source,
    statement: typeof obj.statement === 'string' ? obj.statement : null,
  };
}

/** One wire connection of a binding, named as the user knows it. */
export type WireRow = {
  key: string;
  role: string;
  signal: string;
  /** Readable ends, as the composer names them (`KA:A1`), or the pattern part where unresolved. */
  from: string;
  to: string;
  values: Record<WireField, WireValue>;
  /** Stored keys of this wire's entry that are not wire fields (kept exactly). */
  otherKeys: string[];
};

function endText(
  config: Configuration,
  pattern: Raw,
  binding: ConfigBinding,
  member: unknown,
): string {
  const m = record(member);
  if (m === null) return '?';
  if (typeof m.slot === 'string') {
    const terminal = typeof m.terminal === 'string' ? m.terminal : '';
    const slot = items(pattern.slots).find((s) => s.id === m.slot);
    const template = typeof slot?.template === 'string' ? slot.template : null;
    const marking =
      templateTerminals(config, template)?.find((t) => t.id === terminal)?.marking || terminal;
    const given = record(record(binding.slots)?.[m.slot]);
    const component = items(config.components).find((c) => c.id === given?.component);
    const named = component ? designationText(component.designation) : '';
    return `${named || m.slot}:${marking}`;
  }
  if (typeof m.point === 'string') {
    const point = items(pattern.points).find((p) => p.id === m.point);
    const role = typeof point?.strip === 'string' ? point.strip : '';
    const stripId = record(binding.strips)?.[role];
    const strip = items(config.strips).find((s) => s.id === stripId);
    const named = strip ? designationText(strip.designation) : '';
    const marking = binding.markings[m.point];
    const shown = typeof marking === 'string' && marking !== '' ? marking : m.point;
    return `${named || role}:${shown}`;
  }
  return '?';
}

/**
 * The wire connections of a binding's pattern, in pattern order, each with the
 * details the binding holds for it. Only `wire` connections make wires.
 */
export function wireRows(config: Configuration, binding: ConfigBinding): WireRow[] {
  const pattern = items(config.patterns).find((p) => p.ref === binding.pattern);
  if (pattern === undefined) return [];
  const wires = record(binding.wires) ?? {};
  const rows: WireRow[] = [];
  for (const net of items(pattern.nets)) {
    const role = typeof net.role === 'string' ? net.role : '';
    const members = Array.isArray(net.members) ? (net.members as unknown[]) : [];
    items(net.connections).forEach((connection, index) => {
      if (connection.via !== 'wire') return;
      const key = `${role}/${String(index)}`;
      const entry = record(wires[key]) ?? {};
      rows.push({
        key,
        role,
        signal: typeof net.signal === 'string' ? net.signal : '',
        from: endText(config, pattern, binding, members[Number(connection.from)]),
        to: endText(config, pattern, binding, members[Number(connection.to)]),
        values: {
          number: wireValue(entry.number),
          cross_section_mm2: wireValue(entry.cross_section_mm2),
          colour: wireValue(entry.colour),
        },
        otherKeys: Object.keys(entry).filter(
          (k) => !(WIRE_FIELDS as readonly string[]).includes(k),
        ),
      });
    });
  }
  return rows;
}

/** Details the binding holds under a key no wire connection of its pattern has (the composer refuses them). */
export function strayWireKeys(config: Configuration, binding: ConfigBinding): string[] {
  const known = new Set(wireRows(config, binding).map((row) => row.key));
  return Object.keys(record(binding.wires) ?? {}).filter((key) => !known.has(key));
}

/** Everything a binding holds as wire details, for a removal to list first. */
export function wireDetailsOf(
  binding: ConfigBinding,
): { key: string; field: string; value: string }[] {
  const out: { key: string; field: string; value: string }[] = [];
  for (const [key, entry] of Object.entries(record(binding.wires) ?? {})) {
    const fields = record(entry);
    if (fields === null) {
      out.push({ key, field: '', value: JSON.stringify(entry) });
      continue;
    }
    for (const [field, raw] of Object.entries(fields)) {
      const value = wireValue(raw);
      out.push({ key, field, value: value.kind === 'given' ? value.value : JSON.stringify(raw) });
    }
  }
  return out;
}

export function hasWireDetails(binding: ConfigBinding): boolean {
  return Object.keys(record(binding.wires) ?? {}).length > 0;
}

/** An object without one key (no dynamic delete). */
function without(obj: Raw, key: string): Raw {
  return Object.fromEntries(Object.entries(obj).filter(([k]) => k !== key));
}

function mapWires(
  config: Configuration,
  bindingId: string,
  change: (wires: Raw) => Raw,
): Configuration {
  const all = items(config.bindings);
  if (!all.some((binding) => binding.id === bindingId)) return config;
  const bindings = all.map((binding) =>
    binding.id === bindingId
      ? { ...binding, wires: change({ ...(record(binding.wires) ?? {}) }) }
      : binding,
  );
  return { ...config, bindings };
}

/**
 * A value the user typed for a field, stored as user-entered. Refused
 * (unchanged) where the field holds a value with another source: that value is
 * replaced only through {@link replaceWithUserValue}. The text is kept as typed
 * (an empty or out-of-bounds value is said as a problem, not dropped).
 */
export function setUserValue(
  config: Configuration,
  bindingId: string,
  key: string,
  field: WireField,
  text: string,
): Configuration {
  const binding = bindingsOf(config).find((item) => item.id === bindingId);
  if (binding === undefined) return config;
  const current = wireValue(record(record(binding.wires)?.[key])?.[field]);
  if (current.kind === 'unreadable') return config;
  if (current.kind === 'given' && current.source !== 'user') return config;
  return mapWires(config, bindingId, (wires) => ({
    ...wires,
    [key]: { ...(record(wires[key]) ?? {}), [field]: { value: text, source: 'user' } },
  }));
}

/** A value with another source (data, an assumption) replaced, by the user's explicit choice. */
export function replaceWithUserValue(
  config: Configuration,
  bindingId: string,
  key: string,
  field: WireField,
  text: string,
): Configuration {
  return mapWires(config, bindingId, (wires) => ({
    ...wires,
    [key]: { ...(record(wires[key]) ?? {}), [field]: { value: text, source: 'user' } },
  }));
}

/** One field removed, by the user's explicit choice; the entry goes when nothing is left in it. */
export function removeWireValue(
  config: Configuration,
  bindingId: string,
  key: string,
  field: string,
): Configuration {
  return mapWires(config, bindingId, (wires) => {
    const stored = record(wires[key]) ?? {};
    if (!(field in stored)) return wires;
    const entry = without(stored, field);
    return Object.keys(entry).length === 0 ? without(wires, key) : { ...wires, [key]: entry };
  });
}

/** Everything under one key removed, by the user's explicit choice (the caller lists it first). */
export function removeWireEntry(
  config: Configuration,
  bindingId: string,
  key: string,
): Configuration {
  return mapWires(config, bindingId, (wires) => {
    return key in wires ? without(wires, key) : wires;
  });
}

export type WireProblem = 'empty' | 'too_long' | 'not_decimal' | 'unreadable' | 'unknown_source';

/** What the composer or the snapshot reader would refuse in a value, or null. */
export function wireValueProblem(field: WireField, value: WireValue): WireProblem | null {
  if (value.kind === 'unset') return null;
  if (value.kind === 'unreadable') return 'unreadable';
  if (!(WIRE_SOURCES as readonly string[]).includes(value.source)) return 'unknown_source';
  if (value.value === '') return 'empty';
  if (value.value.length > MAX_WIRE_TEXT) return 'too_long';
  if (field === 'cross_section_mm2') {
    if (!POSITIVE_DECIMAL.test(value.value) || Number(value.value) <= 0) return 'not_decimal';
  }
  return null;
}

export type WireProblems = {
  /** By `${key}|${field}`. */
  values: Record<string, WireProblem>;
  /** Keys under which details are held for no wire connection of the pattern. */
  stray: string[];
  /** `${key}|${name}`: a stored name in a wire's entry that is not a wire field (the composer refuses it). */
  extra: string[];
};

export function wireProblems(config: Configuration, binding: ConfigBinding): WireProblems {
  const out: WireProblems = { values: {}, stray: strayWireKeys(config, binding), extra: [] };
  for (const row of wireRows(config, binding)) {
    for (const name of row.otherKeys) out.extra.push(`${row.key}|${name}`);
    for (const field of WIRE_FIELDS) {
      const problem = wireValueProblem(field, row.values[field]);
      if (problem !== null) out.values[`${row.key}|${field}`] = problem;
    }
  }
  return out;
}

export function wireProblemCount(problems: WireProblems): number {
  return Object.keys(problems.values).length + problems.stray.length + problems.extra.length;
}
