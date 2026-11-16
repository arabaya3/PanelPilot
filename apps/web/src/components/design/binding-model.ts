/**
 * M9b1: pattern bindings the user creates in the guided editor, kept in the
 * same configuration document (`configuration.bindings`, the composer's shape;
 * docs/architecture/m9b1-guided-bindings.md).
 *
 * Every function here is pure and returns a new document; a refused change
 * returns it unchanged. Nothing is inferred: no slot, strip, marking, cable or
 * connection is filled in, and keys this slice does not edit (`wires`, a slot
 * filled by a design device) are kept exactly. The checks below only say which
 * references a save would be refused for; the composer still decides.
 */
import {
  bindingsOf,
  type ConfigBinding,
  type Configuration,
  designationText,
  removeEntry,
  updateEntry,
} from '@/components/design/configuration-model';
import { deriveId } from '@/lib/sha256';

/** What the editor knows of a board from the opened design: its circuits and cables. */
export type BoardChoices = {
  id: string;
  circuits: { id: string; label: string }[];
  cables: { id: string; label: string }[];
};

/** A binding id as the composer and the editor accept it. */
export const BINDING_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/;

type Raw = Record<string, unknown>;

function record(value: unknown): Raw | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Raw)
    : null;
}

function items(value: unknown): Raw[] {
  return Array.isArray(value) ? value.map(record).filter((item): item is Raw => item !== null) : [];
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

/** A pattern as a binding needs it: its slots, strip roles, points and connections. */
export type BindablePattern = {
  ref: string;
  slots: { id: string; template: string | null; deviceKind: string | null }[];
  roles: string[];
  points: { id: string; role: string }[];
  /** Per net role: its connections' via, in order (the composer derives their ids from the index). */
  connections: { role: string; index: number; via: string }[];
  needsCable: boolean;
};

export function bindablePatterns(config: Configuration): BindablePattern[] {
  return items(config.patterns).flatMap((raw) => {
    const ref = text(raw.ref);
    if (ref === null) return [];
    const connections = items(raw.nets).flatMap((net) =>
      items(net.connections).map((connection, index) => ({
        role: typeof net.role === 'string' ? net.role : '',
        index,
        via: typeof connection.via === 'string' ? connection.via : '',
      })),
    );
    return [
      {
        ref,
        slots: items(raw.slots).flatMap((slot) => {
          const id = text(slot.id);
          return id === null
            ? []
            : [{ id, template: text(slot.template), deviceKind: text(slot.device_kind) }];
        }),
        roles: (Array.isArray(raw.strips) ? (raw.strips as unknown[]) : []).filter(
          (role): role is string => typeof role === 'string',
        ),
        points: items(raw.points).flatMap((point) => {
          const id = text(point.id);
          return id === null
            ? []
            : [{ id, role: typeof point.strip === 'string' ? point.strip : '' }];
        }),
        connections,
        needsCable: connections.some((connection) => connection.via === 'cable_core'),
      },
    ];
  });
}

/** Why an id typed for a new binding cannot be used, or null. */
export function bindingIdProblem(
  config: Configuration,
  id: string,
): 'empty' | 'invalid' | 'taken' | null {
  const typed = id.trim();
  if (typed === '') return 'empty';
  if (!BINDING_ID.test(typed)) return 'invalid';
  return bindingsOf(config).some((binding) => binding.id === typed) ? 'taken' : null;
}

/** A new binding: nothing mapped, no marking, no cable. */
export function addBinding(
  config: Configuration,
  entry: { id: string; pattern: string; board: string; circuit: string | null },
): Configuration {
  if (bindingIdProblem(config, entry.id) !== null || entry.pattern === '' || entry.board === '') {
    return config;
  }
  const binding = {
    id: entry.id.trim(),
    pattern: entry.pattern,
    board: entry.board,
    circuit: entry.circuit,
    slots: {},
    strips: {},
    markings: {},
    cable: null,
    wires: {},
  };
  return { ...config, bindings: [...items(config.bindings), binding] };
}

function bindingRaw(config: Configuration, id: string): Raw | undefined {
  return items(config.bindings).find((item) => item.id === id);
}

/** A binding's board or circuit changed. Its slots, strips and markings are kept (and checked). */
export function updateBinding(
  config: Configuration,
  id: string,
  patch: Partial<{ board: string; circuit: string | null; cable: string | null }>,
): Configuration {
  return updateEntry(config, 'bindings', id, patch);
}

/** A slot filled by a configuration component, or emptied (null). Other slots are kept as stored. */
export function setSlot(
  config: Configuration,
  id: string,
  slot: string,
  component: string | null,
): Configuration {
  const raw = bindingRaw(config, id);
  if (raw === undefined) return config;
  const slots: Raw = Object.fromEntries(
    Object.entries(record(raw.slots) ?? {}).filter(([key]) => key !== slot),
  );
  if (component !== null) slots[slot] = { component };
  return updateEntry(config, 'bindings', id, { slots });
}

/** A strip role played by a strip, or left unmapped (null). */
export function setRole(
  config: Configuration,
  id: string,
  role: string,
  strip: string | null,
): Configuration {
  const raw = bindingRaw(config, id);
  if (raw === undefined) return config;
  const strips: Raw = Object.fromEntries(
    Object.entries(record(raw.strips) ?? {}).filter(([key]) => key !== role),
  );
  if (strip !== null) strips[role] = strip;
  return updateEntry(config, 'bindings', id, { strips });
}

export function removeBinding(config: Configuration, id: string): Configuration {
  return removeEntry(config, 'bindings', id);
}

/** What a binding maps now, as stored (a design-device slot is said as such and never changed). */
export type BindingState = {
  slots: Record<string, { component: string } | { device: string } | { other: true }>;
  strips: Record<string, string>;
  cable: string | null;
};

export function bindingState(binding: ConfigBinding): BindingState {
  const slots: BindingState['slots'] = {};
  for (const [slot, value] of Object.entries(record(binding.slots) ?? {})) {
    const given = record(value);
    if (given !== null && typeof given.component === 'string')
      slots[slot] = { component: given.component };
    else if (given !== null && typeof given.device === 'string')
      slots[slot] = { device: given.device };
    else slots[slot] = { other: true };
  }
  const strips: Record<string, string> = {};
  for (const [role, value] of Object.entries(record(binding.strips) ?? {})) {
    if (typeof value === 'string') strips[role] = value;
  }
  return { slots, strips, cable: typeof binding.cable === 'string' ? binding.cable : null };
}

/** The configuration components a slot may take: on the binding's board, on the slot's template. */
export function componentChoices(
  config: Configuration,
  board: string,
  template: string | null,
): { id: string; label: string }[] {
  return items(config.components)
    .filter((item) => item.board === board && (template === null || item.template === template))
    .flatMap((item) =>
      typeof item.id === 'string'
        ? [{ id: item.id, label: designationText(item.designation) || item.id }]
        : [],
    );
}

/** The configuration strips a role may take: on the binding's board. */
export function stripChoices(
  config: Configuration,
  board: string,
): { id: string; label: string }[] {
  return items(config.strips)
    .filter((item) => item.board === board)
    .flatMap((item) =>
      typeof item.id === 'string'
        ? [{ id: item.id, label: designationText(item.designation) || item.id }]
        : [],
    );
}

export type BindingProblem =
  | { field: 'pattern'; code: 'no_pattern' }
  | { field: 'board'; code: 'no_board' }
  | { field: 'circuit'; code: 'no_circuit' }
  | { field: 'cable'; code: 'no_cable' | 'cable_needed' }
  | { field: 'slot'; key: string; code: 'not_chosen' | 'gone' | 'other_board' | 'other_template' }
  | { field: 'role'; key: string; code: 'not_chosen' | 'gone' | 'other_board' }
  | { field: 'marking'; key: string; code: 'missing' };

/**
 * The references a save of this binding would be refused for, found before
 * saving. `boards` is null when the opened design is not known (then circuits
 * and cables are not checked here; the server still checks them).
 */
export function bindingProblems(
  config: Configuration,
  binding: ConfigBinding,
  boards: BoardChoices[] | null,
): BindingProblem[] {
  const out: BindingProblem[] = [];
  const pattern = bindablePatterns(config).find((item) => item.ref === binding.pattern);
  if (pattern === undefined) {
    out.push({ field: 'pattern', code: 'no_pattern' });
    return out;
  }
  const board = boards?.find((item) => item.id === binding.board);
  if (boards !== null && board === undefined) out.push({ field: 'board', code: 'no_board' });
  if (
    board !== undefined &&
    binding.circuit !== null &&
    !board.circuits.some((c) => c.id === binding.circuit)
  ) {
    out.push({ field: 'circuit', code: 'no_circuit' });
  }
  const state = bindingState(binding);
  const components = new Map(items(config.components).map((item) => [String(item.id), item]));
  for (const slot of pattern.slots) {
    const given = state.slots[slot.id];
    if (given === undefined) {
      // A slot with a device kind can be filled from the circuit by the composer.
      if (!(slot.deviceKind !== null && binding.circuit !== null)) {
        out.push({ field: 'slot', key: slot.id, code: 'not_chosen' });
      }
      continue;
    }
    if (!('component' in given)) continue;
    const component = components.get(given.component);
    if (component === undefined) out.push({ field: 'slot', key: slot.id, code: 'gone' });
    else if (component.board !== binding.board) {
      out.push({ field: 'slot', key: slot.id, code: 'other_board' });
    } else if (slot.template !== null && component.template !== slot.template) {
      out.push({ field: 'slot', key: slot.id, code: 'other_template' });
    }
  }
  const strips = new Map(items(config.strips).map((item) => [String(item.id), item]));
  for (const role of pattern.roles) {
    const given = state.strips[role];
    if (given === undefined) out.push({ field: 'role', key: role, code: 'not_chosen' });
    else if (!strips.has(given)) out.push({ field: 'role', key: role, code: 'gone' });
    else if (strips.get(given)?.board !== binding.board) {
      out.push({ field: 'role', key: role, code: 'other_board' });
    }
  }
  for (const point of pattern.points) {
    const marking = binding.markings[point.id];
    if (typeof marking !== 'string' || marking === '') {
      out.push({ field: 'marking', key: point.id, code: 'missing' });
    }
  }
  if (pattern.needsCable && state.cable === null)
    out.push({ field: 'cable', code: 'cable_needed' });
  if (
    board !== undefined &&
    state.cable !== null &&
    !board.cables.some((c) => c.id === state.cable)
  ) {
    out.push({ field: 'cable', code: 'no_cable' });
  }
  return out;
}

/** The bindings that name a component, strip or pattern (for a removal or rename to say first). */
export function bindingUsesOf(
  config: Configuration,
  kind: 'component' | 'strip' | 'pattern',
  id: string,
): string[] {
  return bindingsOf(config)
    .filter((binding) => {
      if (kind === 'pattern') return binding.pattern === id;
      const state = bindingState(binding);
      if (kind === 'component') {
        return Object.values(state.slots).some(
          (slot) => 'component' in slot && slot.component === id,
        );
      }
      return Object.values(state.strips).includes(id);
    })
    .map((binding) => binding.id);
}

/**
 * The ids the composer gives what this binding makes (compose.py), so a preview
 * shows only this binding's rows even when other bindings use the same pattern.
 */
export function bindingRowIds(
  config: Configuration,
  binding: ConfigBinding,
): { connections: Set<string>; wires: Set<string>; points: Set<string>; nets: Set<string> } {
  const pattern = bindablePatterns(config).find((item) => item.ref === binding.pattern);
  const out = {
    connections: new Set<string>(),
    wires: new Set<string>(),
    points: new Set<string>(),
    nets: new Set<string>(),
  };
  if (pattern === undefined) return out;
  for (const connection of pattern.connections) {
    out.connections.add(deriveId('x', binding.id, connection.role, String(connection.index)));
    if (connection.via === 'wire') {
      out.wires.add(deriveId('w', binding.id, connection.role, String(connection.index)));
    }
  }
  const state = bindingState(binding);
  for (const point of pattern.points) {
    const strip = state.strips[point.role];
    if (strip !== undefined) out.points.add(deriveId('p', strip, binding.id, point.id));
  }
  for (const net of items(items(config.patterns).find((p) => p.ref === binding.pattern)?.nets)) {
    if (net.scope === 'binding' && typeof net.role === 'string') {
      out.nets.add(deriveId('n', binding.id, net.role));
    } else if (net.scope === 'board' && typeof net.signal === 'string') {
      // One net per board and signal, shared with other bindings on the board.
      out.nets.add(deriveId('n', binding.board, net.signal));
    }
  }
  return out;
}
