/**
 * M8: component definitions the user enters in the guided editor, kept in the
 * same configuration document (`configuration.templates`, the composer's
 * template shape; docs/architecture/m8-guided-definitions.md).
 *
 * Every function here is pure and returns a new document. Nothing is
 * inferred: the function and each terminal's kind stay `unspecified` unless
 * the user picks a value already present in the project's data; requiredness
 * stays unstated (`null`) unless the user states it; there is no source
 * document unless the user names one; and every definition entered here is
 * `unreviewed`. No rating, parameter, symbol, contact group or connection is
 * added. Terminal ids are made once and never reused, so a feeder link that
 * named a removed terminal can never silently land on another.
 */
import type { Configuration } from '@/components/design/configuration-model';
import type { V2ModelSummary } from '@/lib/design';
import { deriveId } from '@/lib/sha256';

/** The value the composer uses for a kind nobody stated. */
export const UNSPECIFIED = 'unspecified';

/** The id part of a definition's ref (`tenant/<id>/v<n>`), as the server accepts it. */
export const DEFINITION_ID = /^[a-z0-9][a-z0-9_.-]{0,99}$/;

/** Who entered a definition, as recorded in its provenance (no account data). */
export const ENTERED_BY = 'user-entered';

export type DefinitionTerminal = {
  id: string;
  marking: string;
  kind: string;
  required: boolean | null;
  group: string | null;
};

export type Definition = {
  ref: string;
  function: string;
  terminals: DefinitionTerminal[];
  provenance: { source: string | null; author: string; created: string };
  review: { status: string };
};

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function items(config: Configuration, key: string): Record<string, unknown>[] {
  const value = config[key];
  return Array.isArray(value)
    ? value.map(record).filter((item): item is Record<string, unknown> => item !== null)
    : [];
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

/** `tenant/<id>/v<n>` read back into its parts, or null for another scope or shape. */
export function refParts(ref: string): { id: string; version: number } | null {
  const match = /^tenant\/([a-z0-9][a-z0-9_.-]{0,99})\/v([1-9][0-9]{0,8})$/.exec(ref);
  return match ? { id: match[1] as string, version: Number(match[2]) } : null;
}

export function definitionRef(id: string, version = 1): string {
  return `tenant/${id}/v${String(version)}`;
}

function terminalsOf(template: Record<string, unknown>): DefinitionTerminal[] {
  const raw = Array.isArray(template.terminals) ? template.terminals : [];
  return raw.flatMap((item: unknown) => {
    const terminal = record(item);
    const id = terminal ? text(terminal.id) : null;
    if (terminal === null || id === null) return [];
    return [
      {
        id,
        marking: typeof terminal.marking === 'string' ? terminal.marking : '',
        kind: text(terminal.kind) ?? UNSPECIFIED,
        required: typeof terminal.required === 'boolean' ? terminal.required : null,
        group: text(terminal.group),
      },
    ];
  });
}

/**
 * The configuration's own definitions that the editor manages: `tenant/` refs.
 * A loaded `global/` definition is kept as it is and only offered for use.
 */
export function definitionsOf(config: Configuration): Definition[] {
  return items(config, 'templates').flatMap((template) => {
    const ref = text(template.ref);
    if (ref === null || refParts(ref) === null) return [];
    const provenance = record(template.provenance);
    return [
      {
        ref,
        function: text(template.function) ?? UNSPECIFIED,
        terminals: terminalsOf(template),
        provenance: {
          source: provenance ? text(provenance.source) : null,
          author: (provenance && text(provenance.author)) ?? ENTERED_BY,
          created: (provenance && text(provenance.created)) ?? '',
        },
        review: { status: text(record(template.review)?.status) ?? 'unreviewed' },
      },
    ];
  });
}

/** The function values already present in the project's data, plus `unspecified`. */
export function functionChoices(config: Configuration): string[] {
  const seen = new Set<string>([UNSPECIFIED]);
  for (const template of items(config, 'templates')) {
    const fn = text(template.function);
    if (fn !== null) seen.add(fn);
  }
  return [...seen];
}

/** The terminal kinds already present in the project's data, plus `unspecified`. */
export function terminalKindChoices(config: Configuration): string[] {
  const seen = new Set<string>([UNSPECIFIED]);
  for (const template of items(config, 'templates')) {
    for (const terminal of terminalsOf(template)) seen.add(terminal.kind);
  }
  return [...seen];
}

/** Why an id typed for a new or renamed definition cannot be used, or null. */
export function definitionIdProblem(
  config: Configuration,
  id: string,
  except: string | null = null,
): 'empty' | 'invalid' | 'taken' | null {
  const typed = id.trim();
  if (typed === '') return 'empty';
  if (!DEFINITION_ID.test(typed)) return 'invalid';
  const taken = items(config, 'templates').some((template) => {
    const ref = text(template.ref);
    if (ref === null || ref === except) return false;
    const parts = refParts(ref);
    return parts !== null && parts.id === typed;
  });
  return taken ? 'taken' : null;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** A new definition: no terminals yet, nothing inferred, unreviewed. */
export function addDefinition(
  config: Configuration,
  entry: { id: string; function: string },
): Configuration {
  if (definitionIdProblem(config, entry.id) !== null) return config;
  const template = {
    ref: definitionRef(entry.id.trim()),
    function: entry.function === '' ? UNSPECIFIED : entry.function,
    terminals: [],
    contact_groups: [],
    parameters: [],
    provenance: { source: null, author: ENTERED_BY, created: today() },
    review: {
      status: 'unreviewed',
      reviewer: null,
      reviewed_at: null,
      standards: [],
      scope_note: null,
    },
    restricted_scope: [],
  };
  return { ...config, templates: [...items(config, 'templates'), template] };
}

function mapTemplate(
  config: Configuration,
  ref: string,
  change: (template: Record<string, unknown>) => Record<string, unknown>,
): Configuration {
  return {
    ...config,
    templates: items(config, 'templates').map((template) =>
      template.ref === ref ? change(template) : template,
    ),
  };
}

/** A definition with its function or source document changed (a blank source is none). */
export function updateDefinition(
  config: Configuration,
  ref: string,
  patch: { function?: string; source?: string },
): Configuration {
  return mapTemplate(config, ref, (template) => {
    const next = { ...template };
    if (patch.function !== undefined) next.function = patch.function || UNSPECIFIED;
    if (patch.source !== undefined) {
      const provenance = record(template.provenance) ?? {};
      next.provenance = {
        ...provenance,
        source: patch.source.trim() === '' ? null : patch.source,
      };
    }
    return next;
  });
}

/** What names a definition: components and strips by id, and patterns (by ref) whose slots use it. */
export type DefinitionUser = { kind: 'component' | 'strip' | 'pattern'; id: string };

function slotsOf(pattern: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(pattern.slots)
    ? pattern.slots.map(record).filter((item): item is Record<string, unknown> => item !== null)
    : [];
}

export function definitionUsers(config: Configuration, ref: string): DefinitionUser[] {
  const out: DefinitionUser[] = [];
  for (const item of items(config, 'components')) {
    if (item.template === ref && typeof item.id === 'string') {
      out.push({ kind: 'component', id: item.id });
    }
  }
  for (const item of items(config, 'strips')) {
    if (item.template === ref && typeof item.id === 'string')
      out.push({ kind: 'strip', id: item.id });
  }
  for (const pattern of items(config, 'patterns')) {
    if (typeof pattern.ref === 'string' && slotsOf(pattern).some((slot) => slot.template === ref)) {
      out.push({ kind: 'pattern', id: pattern.ref });
    }
  }
  return out;
}

/** The patterns whose nets join this terminal of the definition (through a slot on it). */
export function terminalPatternUses(
  config: Configuration,
  ref: string,
  terminalId: string,
): string[] {
  const out: string[] = [];
  for (const pattern of items(config, 'patterns')) {
    const slots = new Set(
      slotsOf(pattern)
        .filter((slot) => slot.template === ref && typeof slot.id === 'string')
        .map((slot) => slot.id as string),
    );
    if (slots.size === 0 || typeof pattern.ref !== 'string') continue;
    const nets = Array.isArray(pattern.nets) ? pattern.nets : [];
    const joined = nets.some((net: unknown) => {
      const members = record(net)?.members;
      return (
        Array.isArray(members) &&
        members.some((member: unknown) => {
          const value = record(member);
          return (
            value !== null &&
            typeof value.slot === 'string' &&
            slots.has(value.slot) &&
            value.terminal === terminalId
          );
        })
      );
    });
    if (joined) out.push(pattern.ref);
  }
  return out;
}

/**
 * A definition renamed, and every component, strip and pattern slot that names it pointed
 * at the new ref in the same step (the caller shows them first). The version
 * is kept. Refused (unchanged) when the new id cannot be used.
 */
export function renameDefinition(config: Configuration, ref: string, id: string): Configuration {
  const parts = refParts(ref);
  if (parts === null || definitionIdProblem(config, id, ref) !== null) return config;
  // Patterns hold their slots' templates; keep their other keys as they are.
  const next = definitionRef(id.trim(), parts.version);
  if (next === ref) return config;
  const repoint = (key: 'components' | 'strips') =>
    items(config, key).map((item) => (item.template === ref ? { ...item, template: next } : item));
  const patterns = items(config, 'patterns').map((pattern) =>
    slotsOf(pattern).some((slot) => slot.template === ref)
      ? {
          ...pattern,
          slots: slotsOf(pattern).map((slot) =>
            slot.template === ref ? { ...slot, template: next } : slot,
          ),
        }
      : pattern,
  );
  return {
    ...mapTemplate(config, ref, (template) => ({ ...template, ref: next })),
    components: repoint('components'),
    strips: repoint('strips'),
    patterns,
  };
}

/** A definition removed; refused (unchanged) while any component, strip or pattern names it. */
export function removeDefinition(config: Configuration, ref: string): Configuration {
  if (definitionUsers(config, ref).length > 0) return config;
  return {
    ...config,
    templates: items(config, 'templates').filter((template) => template.ref !== ref),
  };
}

function token(): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * A new terminal id: `t-<sequence>-<token>`. The zero-padded sequence (one past
 * the highest the definition holds) keeps the composer's id order equal to the
 * order of entry; the random token means an id is never reissued, even after
 * the highest terminal is removed, so a feeder link that named a removed
 * terminal cannot silently land on a new one.
 */
function nextTerminalId(terminals: DefinitionTerminal[]): string {
  let highest = 0;
  for (const terminal of terminals) {
    const match = /^t-(\d+)/.exec(terminal.id);
    if (match) highest = Math.max(highest, Number(match[1]));
  }
  const used = new Set(terminals.map((terminal) => terminal.id));
  let id = `t-${String(highest + 1).padStart(3, '0')}-${token()}`;
  while (used.has(id)) id = `t-${String(highest + 1).padStart(3, '0')}-${token()}`;
  return id;
}

/**
 * A definition with a terminal added. Its marking is the user's own text; its
 * kind and requiredness are as chosen (default unspecified / not stated).
 */
export function addDefinitionTerminal(
  config: Configuration,
  ref: string,
  entry: { marking: string; kind?: string; required?: boolean | null },
): Configuration {
  return mapTemplate(config, ref, (template) => {
    const terminals = terminalsOf(template);
    const added: DefinitionTerminal = {
      id: nextTerminalId(terminals),
      marking: entry.marking,
      kind: entry.kind && entry.kind !== '' ? entry.kind : UNSPECIFIED,
      required: entry.required ?? null,
      group: null,
    };
    return { ...template, terminals: [...terminals, added] };
  });
}

export function updateDefinitionTerminal(
  config: Configuration,
  ref: string,
  terminalId: string,
  patch: { marking?: string; kind?: string; required?: boolean | null },
): Configuration {
  return mapTemplate(config, ref, (template) => ({
    ...template,
    terminals: terminalsOf(template).map((terminal) =>
      terminal.id === terminalId
        ? {
            ...terminal,
            ...(patch.marking !== undefined ? { marking: patch.marking } : {}),
            ...(patch.kind !== undefined ? { kind: patch.kind || UNSPECIFIED } : {}),
            ...(patch.required !== undefined ? { required: patch.required } : {}),
          }
        : terminal,
    ),
  }));
}

export function removeDefinitionTerminal(
  config: Configuration,
  ref: string,
  terminalId: string,
): Configuration {
  return mapTemplate(config, ref, (template) => ({
    ...template,
    terminals: terminalsOf(template).filter((terminal) => terminal.id !== terminalId),
  }));
}

export type DefinitionProblems = {
  /** Terminal id → why its marking cannot be saved. */
  terminals: Record<string, 'empty' | 'duplicate'>;
};

/** What in a definition the server would refuse, said per field before any save. */
export function definitionProblems(definition: Definition): DefinitionProblems {
  const terminals: Record<string, 'empty' | 'duplicate'> = {};
  const counts = new Map<string, number>();
  for (const terminal of definition.terminals) {
    const marking = terminal.marking;
    counts.set(marking, (counts.get(marking) ?? 0) + 1);
  }
  for (const terminal of definition.terminals) {
    if (terminal.marking.trim() === '') terminals[terminal.id] = 'empty';
    else if ((counts.get(terminal.marking) ?? 0) > 1) terminals[terminal.id] = 'duplicate';
  }
  return { terminals };
}

/** A feeder-link end as the link form holds it (`encodeTerminal` JSON), read back. */
export type LinkEndUse = {
  /** The fed board the link belongs to. */
  fedBoard: string;
  role: 'from' | 'to';
  terminal: { component: string; terminal: string } | { point: string; side: string };
};

/** The model id the server derives for a configuration component. */
export function componentModelId(componentId: string): string {
  return deriveId('k', componentId);
}

/** The model id the server derives for a point entered directly on a strip. */
export function directPointModelId(stripId: string, pointId: string): string {
  return deriveId('p', stripId, 'direct', pointId);
}

/** The feeder-link ends that name a terminal of this definition (through any component on it). */
export function terminalLinkUses(
  config: Configuration,
  ref: string,
  terminalId: string,
  uses: LinkEndUse[],
): LinkEndUse[] {
  const components = new Set(
    items(config, 'components')
      .filter((item) => item.template === ref && typeof item.id === 'string')
      .map((item) => componentModelId(item.id as string)),
  );
  return uses.filter(
    (use) =>
      'component' in use.terminal &&
      components.has(use.terminal.component) &&
      use.terminal.terminal === terminalId,
  );
}

/** The feeder-link ends that name any terminal of one configuration component. */
export function componentLinkUses(componentId: string, uses: LinkEndUse[]): LinkEndUse[] {
  const modelId = componentModelId(componentId);
  return uses.filter((use) => 'component' in use.terminal && use.terminal.component === modelId);
}

/** The feeder-link ends that name a point of one configuration strip (entered directly). */
export function stripLinkUses(
  stripId: string,
  pointIds: string[],
  uses: LinkEndUse[],
): LinkEndUse[] {
  const points = new Set(pointIds.map((point) => directPointModelId(stripId, point)));
  return uses.filter((use) => 'point' in use.terminal && points.has(use.terminal.point));
}

/** Whether the model already holds a definition this configuration does not (shown read-only). */
export function registryOnly(model: V2ModelSummary | null, config: Configuration): string[] {
  const own = new Set(items(config, 'templates').map((template) => text(template.ref)));
  return (model?.definitions ?? [])
    .filter((definition) => definition.kind === 'template' && !own.has(definition.ref))
    .map((definition) => definition.ref);
}
