/**
 * M7a: the board-to-board link of a feeder (docs/architecture/m7a-feeder-link.md).
 *
 * A link records what the user knows about the connection between a supply
 * board and the board it feeds: each end (a modelled terminal of that board,
 * or the user's own label, or nothing) and the cable's known properties. It
 * adds no engineering value and decides nothing about completeness: a blank
 * stays "not given", and nothing is ever prefilled from the designer's feeder
 * cable.
 */
import type { V2ModelSummary } from '@/lib/design';

/** The cable/conductor properties a link can carry, in the contract's order. */
export const LINK_ATTRIBUTES = [
  'cores',
  'cross_section_mm2',
  'material',
  'insulation',
  'length_m',
  'number',
  'colour',
] as const;
export type LinkAttribute = (typeof LINK_ATTRIBUTES)[number];

/** The attributes the feeder calculation also has a value for. */
export const CALCULATED_ATTRIBUTES: ReadonlySet<LinkAttribute> = new Set<LinkAttribute>([
  'cores',
  'cross_section_mm2',
  'material',
  'insulation',
  'length_m',
]);

/** The conductor metals the calculation knows. */
export const LINK_MATERIALS = ['copper', 'aluminium'] as const;

/** A modelled terminal: a component's terminal or a strip point's side (`TerminalRef`). */
export type TerminalRef =
  { component: string; terminal: string } | { point: string; side: 'inner' | 'outer' };

/** One end as the form edits it: an encoded terminal (or '') and a label (or ''). */
export type LinkEndForm = { terminal: string; label: string };

/** A feeder's link as the form edits it; every field may stay empty. */
export type LinkForm = {
  from: LinkEndForm;
  to: LinkEndForm;
  attributes: Record<LinkAttribute, string>;
};

export function blankLink(): LinkForm {
  return {
    from: { terminal: '', label: '' },
    to: { terminal: '', label: '' },
    attributes: {
      cores: '',
      cross_section_mm2: '',
      material: '',
      insulation: '',
      length_m: '',
      number: '',
      colour: '',
    },
  };
}

/** Whether nothing at all is entered (a board then keeps no link in the form). */
export function isBlankLink(link: LinkForm | undefined): boolean {
  if (link === undefined) return true;
  const ends = [link.from, link.to].every((end) => end.terminal === '' && end.label.trim() === '');
  return ends && LINK_ATTRIBUTES.every((key) => link.attributes[key].trim() === '');
}

/**
 * Whether a terminal names a component's terminal (not a strip point).
 * The API sends both pairs with the unused one null, so the test is the
 * value, not the key's presence.
 */
export function isComponentTerminal(
  ref: TerminalRef,
): ref is { component: string; terminal: string } {
  return typeof (ref as { component?: unknown }).component === 'string';
}

/** A terminal as one string, for a select's value (and back). */
export function encodeTerminal(ref: TerminalRef): string {
  return isComponentTerminal(ref)
    ? JSON.stringify({ component: ref.component, terminal: ref.terminal })
    : JSON.stringify({ point: ref.point, side: ref.side });
}

export function decodeTerminal(text: string): TerminalRef | null {
  if (text === '') return null;
  try {
    const value = JSON.parse(text) as Record<string, unknown>;
    if (typeof value.component === 'string' && typeof value.terminal === 'string') {
      return { component: value.component, terminal: value.terminal };
    }
    if (typeof value.point === 'string' && (value.side === 'inner' || value.side === 'outer')) {
      return { point: value.point, side: value.side };
    }
  } catch {
    // Not one this page wrote.
  }
  return null;
}

export type TerminalOption = { value: string; text: string };

/**
 * The modelled terminals and strip points of one board, from the opened
 * revision's v2/v3 model: a link end may only name a terminal of its own board.
 */
export function terminalOptions(model: V2ModelSummary | null, board: string): TerminalOption[] {
  if (model === null) return [];
  const options: TerminalOption[] = [];
  for (const row of model.terminals) {
    if (row.board_id !== board) continue;
    const owner = row.designation ?? row.owner_id;
    if (row.owner_kind === 'component') {
      options.push({
        value: encodeTerminal({ component: row.owner_id, terminal: row.terminal_id }),
        text: `${owner}:${row.marking}`,
      });
    } else if (row.owner_kind === 'strip' && (row.side === 'inner' || row.side === 'outer')) {
      options.push({
        value: encodeTerminal({ point: row.terminal_id, side: row.side }),
        text: `${owner}:${row.marking} (${row.side})`,
      });
    }
  }
  return options;
}

/** A terminal said as the model names it, or its raw ids where the model does not hold it. */
export function terminalText(ref: TerminalRef, model: V2ModelSummary | null): string {
  if (model !== null) {
    for (const row of model.terminals) {
      const same = isComponentTerminal(ref)
        ? row.owner_kind === 'component' &&
          row.owner_id === ref.component &&
          row.terminal_id === ref.terminal
        : row.owner_kind === 'strip' && row.terminal_id === ref.point && row.side === ref.side;
      if (same) {
        const owner = row.designation ?? row.owner_id;
        return isComponentTerminal(ref)
          ? `${owner}:${row.marking}`
          : `${owner}:${row.marking} (${ref.side})`;
      }
    }
  }
  return isComponentTerminal(ref)
    ? `${ref.component}:${ref.terminal}`
    : `${ref.point} (${ref.side})`;
}

/** One entry of the request's `feeder_links`, exactly as the contract states it (§4). */
export type FeederLinkEntry = {
  fed_board: string;
  from_end?: { terminal?: TerminalRef; label?: string };
  to_end?: { terminal?: TerminalRef; label?: string };
  attributes?: Partial<Record<LinkAttribute, string | number>>;
};

function endEntry(end: LinkEndForm): FeederLinkEntry['from_end'] {
  const terminal = decodeTerminal(end.terminal);
  const label = end.label.trim();
  if (terminal === null && label === '') return undefined;
  return { ...(terminal ? { terminal } : {}), ...(label !== '' ? { label } : {}) };
}

/**
 * The request's `feeder_links` for the boards entered: one entry per fed
 * board with something entered. Blank fields are left out (unset), never
 * defaulted; cores goes as a whole number, every other value as typed.
 */
export function feederLinksOf(
  boards: ReadonlyArray<{ name: string; fedFrom: string; link?: LinkForm }>,
): FeederLinkEntry[] {
  const entries: FeederLinkEntry[] = [];
  for (const board of boards) {
    if (board.fedFrom.trim() === '' || isBlankLink(board.link) || board.link === undefined) {
      continue;
    }
    const link = board.link;
    const attributes: Partial<Record<LinkAttribute, string | number>> = {};
    for (const key of LINK_ATTRIBUTES) {
      const value = link.attributes[key].trim();
      if (value === '') continue;
      const whole = Number(value);
      attributes[key] = key === 'cores' && /^\d+$/.test(value) ? whole : value;
    }
    const from = endEntry(link.from);
    const to = endEntry(link.to);
    entries.push({
      fed_board: board.name.trim(),
      ...(from ? { from_end: from } : {}),
      ...(to ? { to_end: to } : {}),
      ...(Object.keys(attributes).length > 0 ? { attributes } : {}),
    });
  }
  return entries;
}

/** A saved request's `feeder_links` back as each fed board's form, by board name. */
export function linkFormsOf(request: object | null | undefined): Map<string, LinkForm> {
  const forms = new Map<string, LinkForm>();
  const raw = (request as { feeder_links?: unknown } | null | undefined)?.feeder_links;
  if (!Array.isArray(raw)) return forms;
  for (const item of raw as unknown[]) {
    if (typeof item !== 'object' || item === null) continue;
    const entry = item as Record<string, unknown>;
    if (typeof entry.fed_board !== 'string') continue;
    const form = blankLink();
    const end = (value: unknown): LinkEndForm => {
      const e = (typeof value === 'object' && value !== null ? value : {}) as Record<
        string,
        unknown
      >;
      const terminal =
        typeof e.terminal === 'object' && e.terminal !== null
          ? decodeTerminal(JSON.stringify(e.terminal))
          : null;
      return {
        terminal: terminal ? encodeTerminal(terminal) : '',
        label: typeof e.label === 'string' ? e.label : '',
      };
    };
    form.from = end(entry.from_end);
    form.to = end(entry.to_end);
    const attributes = (entry.attributes ?? {}) as Record<string, unknown>;
    for (const key of LINK_ATTRIBUTES) {
      const value = attributes[key];
      if (typeof value === 'string' || typeof value === 'number') {
        form.attributes[key] = String(value);
      }
    }
    if (!isBlankLink(form)) forms.set(entry.fed_board, form);
  }
  return forms;
}

/** A draft's stored link, or undefined for anything this page did not write. */
export function parseLink(value: unknown): LinkForm | undefined {
  if (typeof value !== 'object' || value === null) return undefined;
  const raw = value as Record<string, unknown>;
  const end = (item: unknown): LinkEndForm | null => {
    if (typeof item !== 'object' || item === null) return null;
    const e = item as Record<string, unknown>;
    return typeof e.terminal === 'string' && typeof e.label === 'string'
      ? { terminal: e.terminal, label: e.label }
      : null;
  };
  const from = end(raw.from);
  const to = end(raw.to);
  if (from === null || to === null) return undefined;
  if (typeof raw.attributes !== 'object' || raw.attributes === null) return undefined;
  const given = raw.attributes as Record<string, unknown>;
  const link = blankLink();
  link.from = from;
  link.to = to;
  for (const key of LINK_ATTRIBUTES) {
    const v = given[key];
    if (v !== undefined && typeof v !== 'string') return undefined;
    link.attributes[key] = v ?? '';
  }
  return isBlankLink(link) ? undefined : link;
}

/** A problem with a typed value the page can say before saving; the server checks again. */
export type LinkValueProblem = 'cores' | 'positive' | 'nonNegative';

export function linkValueProblem(key: LinkAttribute, text: string): LinkValueProblem | null {
  const value = text.trim();
  if (value === '') return null;
  if (key === 'cores') {
    const n = Number(value);
    return /^\d+$/.test(value) && n >= 1 && n <= 100 ? null : 'cores';
  }
  if (key === 'cross_section_mm2') {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? null : 'positive';
  }
  if (key === 'length_m') {
    const n = Number(value);
    return Number.isFinite(n) && n >= 0 ? null : 'nonNegative';
  }
  return null;
}

/** The values the feeder calculation used, by attribute; null where it has none. */
export type CalculatedValues = Partial<Record<LinkAttribute, string | null>>;

type DesignedBoard = {
  name: string;
  feeder_length_m?: string | number | null;
  circuits?: Array<{ feeds?: string | null; cable_id?: string | null }>;
  cables?: Array<{
    id: string;
    cores: number;
    cross_section_mm2: string | number;
    material: string;
    insulation: string;
    length_m?: string | number | null;
  }>;
};

/**
 * What the feeder calculation used for the cable from `supply` to `fed`, from
 * a designed project: the supply board's circuit that feeds the board, and
 * its cable. Read-only: never copied into the link.
 */
export function calculatedValues(
  design: { boards?: unknown } | null | undefined,
  supply: string,
  fed: string,
): CalculatedValues | null {
  const boards = (design?.boards ?? []) as DesignedBoard[];
  const from = boards.find((board) => board.name === supply);
  const to = boards.find((board) => board.name === fed);
  const circuit = from?.circuits?.find((entry) => entry.feeds === fed);
  const cable = circuit?.cable_id
    ? from?.cables?.find((entry) => entry.id === circuit.cable_id)
    : undefined;
  if (!cable) return null;
  const length = cable.length_m ?? to?.feeder_length_m ?? null;
  return {
    cores: String(cable.cores),
    cross_section_mm2: String(cable.cross_section_mm2),
    material: cable.material,
    insulation: cable.insulation,
    length_m: length === null ? null : String(length),
  };
}

/** Whether a user value and the calculation's differ: numbers by value, text ignoring case. */
export function valuesDiffer(
  key: LinkAttribute,
  user: string,
  calculated: string | null | undefined,
): boolean {
  const a = user.trim();
  if (a === '' || calculated === null || calculated === undefined) return false;
  const b = calculated.trim();
  if (key === 'cores' || key === 'cross_section_mm2' || key === 'length_m') {
    const x = Number(a);
    const y = Number(b);
    if (Number.isFinite(x) && Number.isFinite(y)) return x !== y;
  }
  if (key === 'material') {
    // The same metal under its name or its symbol: the form offers
    // "copper"/"aluminium", the designer stores "Cu"/"Al".
    const metal = (value: string) => METALS[value.toLowerCase()] ?? value.toLowerCase();
    return metal(a) !== metal(b);
  }
  return a.toLowerCase() !== b.toLowerCase();
}

/** A conductor metal's name and its chemical symbol, compared as one. */
const METALS: Record<string, string> = {
  copper: 'cu',
  cu: 'cu',
  aluminium: 'al',
  aluminum: 'al',
  al: 'al',
};

/* ---------------------------------------------------------------------- */
/* A stored link, as a saved revision returns it (§3).                     */
/* ---------------------------------------------------------------------- */

export type StoredLinkEnd = {
  role: 'from' | 'to';
  board: string;
  terminal: TerminalRef | null;
  label: string | null;
  resolution: 'resolved' | 'unresolved';
};

export type StoredLinkAttribute = { value: string | number | null; source: 'user' | 'unset' };

export type StoredLink = {
  id: string;
  origin: string;
  feeder: { supply_board: string; fed_board: string; circuit_id: string | null };
  ends: StoredLinkEnd[];
  carrier: { kind: 'cable' | 'unknown'; cable_id: string | null };
  attributes: Partial<Record<LinkAttribute, StoredLinkAttribute>>;
  provenance: {
    requirement_ref: string;
    entered_by_user_id?: string | null;
    entered_at?: string | null;
  };
};

/**
 * The links a saved revision holds: the project's `links` (or its model's,
 * where the server puts them there). Empty for a v1/v2 revision.
 */
export function storedLinksOf(project: object | null | undefined): StoredLink[] {
  const p = project as {
    links?: unknown;
    model_v3?: { links?: unknown } | null;
    model_v2?: { links?: unknown } | null;
  } | null;
  const raw = p?.links ?? p?.model_v3?.links ?? p?.model_v2?.links;
  if (!Array.isArray(raw)) return [];
  return (raw as unknown[]).filter(
    (item): item is StoredLink =>
      typeof item === 'object' &&
      item !== null &&
      typeof (item as StoredLink).id === 'string' &&
      Array.isArray((item as StoredLink).ends) &&
      typeof (item as StoredLink).feeder === 'object',
  );
}

/** The link finding codes (§5). */
export const LINK_FINDINGS = [
  'link_endpoint_unresolved',
  'link_attribute_unset',
  'link_value_differs_from_calculation',
] as const;

export type LinkFinding = {
  code: string;
  severity?: string;
  message?: string;
  board_id?: string | null;
  params?: Record<string, unknown> | null;
  /** As the API sends them (`DesignFinding.values`). */
  values?: Record<string, unknown> | null;
};

/** A revision's link findings, from its findings list. */
export function linkFindingsOf(findings: readonly object[] | null | undefined): LinkFinding[] {
  return (findings ?? []).filter((finding): finding is LinkFinding => {
    const code: unknown = (finding as { code?: unknown }).code;
    return typeof code === 'string' && (LINK_FINDINGS as readonly string[]).includes(code);
  });
}

/** The schema whose deployments take `feeder_links`. */
export const LINK_SCHEMA = 'pp-design/3';

/**
 * Whether the opened project says this deployment writes `pp-design/3`:
 * the server's `written_schema`, or a revision stored in v3. Anything else
 * (or no project open) shows nothing new.
 */
export function writesLinks(project: object | null | undefined): boolean {
  const p = project as { written_schema?: unknown; snapshot_schema?: unknown } | null;
  return p?.written_schema === LINK_SCHEMA || p?.snapshot_schema === LINK_SCHEMA;
}
