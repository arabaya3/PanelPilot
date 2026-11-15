import { CONTROL_NEEDS, type ControlNeed } from '@/lib/design';
import { DESIGN_DRAFT_STORAGE_KEY } from '@/lib/trial';
import { parseLink } from '@/components/design/feeder-link-model';
import { normalized, type Configuration } from '@/components/design/configuration-model';
import {
  DRAWING_LANGUAGES,
  EARTHING,
  LOAD_KINDS,
  STARTERS,
  type BoardForm,
  type DrawingLanguage,
  type Load,
  type ProjectInfo,
} from '@/components/design/schedule';

/**
 * Unsaved work on `/design`, kept in this tab's session storage while it is
 * unsaved, so it can be offered back after the page was left in a way the
 * unsaved-changes guard cannot hold (a jump several entries back through the
 * browser's history menu, a reload the engineer confirmed).
 *
 * It holds only what the engineer typed and which saved project it belongs
 * to -- never a token or the trial's claim secret -- and it is tied to the
 * trial session it was typed in: a draft from another session is removed
 * unread, never offered.
 */

export const DRAFT_STORAGE_KEY = DESIGN_DRAFT_STORAGE_KEY;
const DRAFT_VERSION = 1;

/** A board as the draft keeps it: without the keys, which a restore numbers afresh. */
export type BoardDraft = Omit<BoardForm, 'key' | 'loads'> & { loads: Omit<Load, 'key'>[] };

export function draftBoards(boards: BoardForm[]): BoardDraft[] {
  return boards.map(({ key: _board, loads, ...board }) => ({
    ...board,
    loads: loads.map(({ key: _load, ...load }) => load),
  }));
}

/** Draft boards back as the form's, keyed from `firstKey` on; `used` is how many keys they took. */
export function formBoards(
  boards: BoardDraft[],
  firstKey: number,
): { boards: BoardForm[]; used: number } {
  let next = firstKey;
  const keyed = boards.map((board) => ({
    ...board,
    key: next++,
    loads: board.loads.map((load) => ({ ...load, key: next++ })),
  }));
  return { boards: keyed, used: next - firstKey };
}

export type DesignDraft = {
  /** The trial session the work was typed in. */
  session: string;
  /** The saved project and revision it edits; null for a project never saved. */
  project: { id: string; revision: number } | null;
  /** The form as it was last saved or opened, so the restored work still reads as unsaved. */
  clean: string;
  info: ProjectInfo;
  boards: BoardDraft[];
  profileText: string;
  language: DrawingLanguage | null;
  market: string;
  /**
   * Each load's control needs, by board and row (keys are numbered afresh on
   * restore). Absent in a draft written before control needs were kept,
   * which still restores, with none ticked.
   */
  control?: ControlNeed[][][];
  /**
   * M6b: the typed project description, the proposal open (re-read from the
   * server on restore) and the control needs placed from it, by position.
   * Absent in an older draft, which restores without them.
   */
  ai?: AiDraft;
  /**
   * M7b: unsaved edits to the opened revision's configuration, and whether
   * "save without that configuration" was ticked. Absent when the
   * configuration was not edited (the restore then reopens the stored one)
   * and in an older draft.
   */
  configuration?: ConfigurationDraft;
};

export type ConfigurationDraft = { doc: Configuration; dropUnrecoverable: boolean };

function parseConfiguration(value: unknown): ConfigurationDraft | undefined {
  if (!isFields(value) || !isFields(value.doc) || typeof value.dropUnrecoverable !== 'boolean') {
    return undefined;
  }
  return { doc: normalized(value.doc), dropUnrecoverable: value.dropUnrecoverable };
}

export type AiDraft = {
  description: string;
  proposalId: string | null;
  controlTags: Array<{ item: string; board: number; row: number; need: ControlNeed }>;
};

/** Control tags by row key, as the draft keeps them: by board and row position. */
export function draftControlTags(
  boards: BoardForm[],
  tags: Readonly<Record<string, { loadKey: number; need: ControlNeed }>>,
): AiDraft['controlTags'] {
  const out: AiDraft['controlTags'] = [];
  for (const [item, tag] of Object.entries(tags)) {
    boards.forEach((board, b) => {
      board.loads.forEach((load, row) => {
        if (load.key === tag.loadKey) out.push({ item, board: b, row, need: tag.need });
      });
    });
  }
  return out;
}

/** A draft's control tags on the restored boards, by their new row keys. */
export function formControlTags(
  boards: BoardForm[],
  tags: AiDraft['controlTags'] | undefined,
): Record<string, { loadKey: number; need: ControlNeed }> {
  const out: Record<string, { loadKey: number; need: ControlNeed }> = {};
  for (const tag of tags ?? []) {
    const load = boards[tag.board]?.loads[tag.row];
    if (load) out[tag.item] = { loadKey: load.key, need: tag.need };
  }
  return out;
}

function parseAi(value: unknown): AiDraft | undefined {
  if (!isFields(value) || typeof value.description !== 'string') return undefined;
  const proposalId =
    typeof value.proposalId === 'string' && value.proposalId !== '' ? value.proposalId : null;
  const tags: AiDraft['controlTags'] = [];
  if (Array.isArray(value.controlTags)) {
    for (const entry of value.controlTags as unknown[]) {
      if (!isFields(entry)) continue;
      const need = CONTROL_NEEDS.find((known) => known === entry.need);
      if (
        typeof entry.item === 'string' &&
        Number.isInteger(entry.board) &&
        Number.isInteger(entry.row) &&
        need
      ) {
        tags.push({
          item: entry.item,
          board: entry.board as number,
          row: entry.row as number,
          need,
        });
      }
    }
  }
  return { description: value.description, proposalId, controlTags: tags };
}

/** Control needs by row key, as the draft keeps them: by board and row position. */
export function draftControl(
  boards: BoardForm[],
  control: Readonly<Record<number, readonly ControlNeed[]>>,
): ControlNeed[][][] {
  return boards.map((board) => board.loads.map((load) => [...(control[load.key] ?? [])]));
}

/** A draft's control needs on the restored boards, by their new row keys. */
export function formControl(
  boards: BoardForm[],
  control: ControlNeed[][][] | undefined,
): Record<number, ControlNeed[]> {
  const needs: Record<number, ControlNeed[]> = {};
  boards.forEach((board, b) => {
    board.loads.forEach((load, l) => {
      const row = control?.[b]?.[l];
      if (row && row.length > 0) needs[load.key] = row;
    });
  });
  return needs;
}

/** A stored control field, or undefined when absent or not one this page wrote. */
function parseControl(value: unknown): ControlNeed[][][] | undefined {
  if (!Array.isArray(value)) return undefined;
  const known = (need: unknown): need is ControlNeed =>
    CONTROL_NEEDS.some((entry) => entry === need);
  const ok = value.every(
    (board) =>
      Array.isArray(board) &&
      board.every((row) => Array.isArray(row) && row.every((need) => known(need))),
  );
  return ok ? value : undefined;
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    // Storage blocked (a privacy setting): there is nowhere to keep a draft.
    return null;
  }
}

/** Keep the draft; false when the browser would not store it. */
export function writeDraft(draft: DesignDraft): boolean {
  const store = storage();
  if (store === null) return false;
  try {
    store.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ v: DRAFT_VERSION, ...draft }));
    return true;
  } catch {
    // Over quota: the guard's warnings still stand, but nothing is kept.
    return false;
  }
}

export function clearDraft(): void {
  try {
    storage()?.removeItem(DRAFT_STORAGE_KEY);
  } catch {
    // Nothing stored can be reached, so nothing is left to offer either.
  }
}

/**
 * The draft typed in `session`, or null. Anything else stored -- another
 * session's draft, an older format, malformed data -- is removed.
 */
export function readDraft(session: string): DesignDraft | null {
  const store = storage();
  if (store === null) return null;
  let raw: string | null;
  try {
    raw = store.getItem(DRAFT_STORAGE_KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    value = null;
  }
  const draft = parseDraft(value);
  if (draft === null || draft.session !== session) {
    clearDraft();
    return null;
  }
  return draft;
}

type Fields = Record<string, unknown>;

function isFields(value: unknown): value is Fields {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function strings<K extends string>(value: Fields, keys: readonly K[]): Record<K, string> | null {
  const out = {} as Record<K, string>;
  for (const key of keys) {
    const field = value[key];
    if (typeof field !== 'string') return null;
    out[key] = field;
  }
  return out;
}

function oneOf<T extends string>(value: unknown, options: readonly T[]): T | null {
  return options.find((option) => option === value) ?? null;
}

function parsePhases(value: unknown): '1' | '3' | null {
  return value === '1' || value === '3' ? value : null;
}

function parseLoad(value: unknown): Omit<Load, 'key'> | null {
  if (!isFields(value)) return null;
  const text = strings(value, ['description', 'power', 'powerFactor', 'length'] as const);
  const load = oneOf(value.load, LOAD_KINDS);
  const phases = parsePhases(value.phases);
  const starter = value.starter === '' ? '' : oneOf(value.starter, STARTERS);
  if (text === null || load === null || phases === null || starter === null) return null;
  if (typeof value.controlled !== 'boolean') return null;
  const aiItem = typeof value.aiItem === 'string' && value.aiItem !== '' ? value.aiItem : null;
  return {
    ...text,
    load,
    phases,
    starter,
    controlled: value.controlled,
    ...(aiItem ? { aiItem } : {}),
  };
}

function parseBoard(value: unknown): BoardDraft | null {
  if (!isFields(value) || !Array.isArray(value.loads)) return null;
  const text = strings(value, [
    'name',
    'location',
    'voltage',
    'faultLevel',
    'earthLoop',
    'fedFrom',
    'feederLength',
  ] as const);
  const phases = parsePhases(value.phases);
  const earthing = oneOf(value.earthing, EARTHING);
  const frequency =
    value.frequency === undefined ? undefined : oneOf(value.frequency, ['50', '60'] as const);
  if (text === null || phases === null || earthing === null || frequency === null) return null;
  const loads: Omit<Load, 'key'>[] = [];
  for (const entry of value.loads) {
    const load = parseLoad(entry);
    if (load === null) return null;
    loads.push(load);
  }
  const aiItem = typeof value.aiItem === 'string' && value.aiItem !== '' ? value.aiItem : null;
  // M7a: a feeder's link form; one this page did not write is left out.
  const link = parseLink(value.link);
  return {
    ...text,
    phases,
    earthing,
    ...(frequency ? { frequency } : {}),
    loads,
    ...(aiItem ? { aiItem } : {}),
    ...(link ? { link } : {}),
  };
}

function parseProject(value: unknown): DesignDraft['project'] | undefined {
  if (value === null) return null;
  if (!isFields(value) || typeof value.id !== 'string' || value.id === '') return undefined;
  const revision = value.revision;
  if (typeof revision !== 'number' || !Number.isInteger(revision) || revision < 1) {
    return undefined;
  }
  return { id: value.id, revision };
}

/** A stored draft in the current format, or null for anything else. */
export function parseDraft(value: unknown): DesignDraft | null {
  if (!isFields(value) || value.v !== DRAFT_VERSION) return null;
  const head = strings(value, ['session', 'clean', 'profileText', 'market'] as const);
  if (head === null || head.session === '') return null;
  const project = parseProject(value.project);
  if (project === undefined) return null;
  if (!isFields(value.info)) return null;
  const info = strings(value.info, [
    'name',
    'number',
    'customer',
    'consultant',
    'contractor',
  ] as const);
  const language = value.language === null ? null : oneOf(value.language, DRAWING_LANGUAGES);
  if (info === null || (value.language !== null && language === null)) return null;
  if (!Array.isArray(value.boards) || value.boards.length === 0) return null;
  const boards: BoardDraft[] = [];
  for (const entry of value.boards) {
    const board = parseBoard(entry);
    if (board === null) return null;
    boards.push(board);
  }
  const control = parseControl(value.control);
  const ai = parseAi(value.ai);
  // Edits to a saved revision's configuration only; without a project they are not kept.
  const configuration = project === null ? undefined : parseConfiguration(value.configuration);
  return {
    ...head,
    project,
    info,
    boards,
    language,
    ...(control ? { control } : {}),
    ...(ai ? { ai } : {}),
    ...(configuration ? { configuration } : {}),
  };
}
