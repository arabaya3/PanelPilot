import { DESIGN_DRAFT_STORAGE_KEY } from '@/lib/trial';
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
};

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
  return { ...text, load, phases, starter, controlled: value.controlled };
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
  return { ...text, phases, earthing, ...(frequency ? { frequency } : {}), loads };
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
  return { ...head, project, info, boards, language };
}
