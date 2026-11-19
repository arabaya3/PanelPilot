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

/** A binding reference kept for the load now at its position (see ReferenceConfirmation). */
export type ConfirmedReference = {
  binding: string;
  field: 'circuit' | 'cable' | 'slot';
  key: string | null;
  id: string;
  /** The refusal's token: binds the confirmation to the exact before and after. */
  token: string;
};

export type ConfigurationDraft = {
  doc: Configuration;
  dropUnrecoverable: boolean;
  /** Kept through a reload with the edits they belong to; absent in older drafts. */
  confirmed?: ConfirmedReference[];
};

function confirmedOf(value: unknown): ConfirmedReference[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item: unknown) => {
    if (!isFields(item)) return [];
    const { binding, field, key, id, token } = item;
    if (typeof binding !== 'string' || typeof id !== 'string') return [];
    // An older draft's confirmation has no token: it is bound to nothing, so it is dropped.
    if (typeof token !== 'string' || token === '') return [];
    if (field !== 'circuit' && field !== 'cable' && field !== 'slot') return [];
    return [{ binding, field, key: typeof key === 'string' ? key : null, id, token }];
  });
}

export function parseConfiguration(value: unknown): ConfigurationDraft | undefined {
  if (!isFields(value) || !isFields(value.doc) || typeof value.dropUnrecoverable !== 'boolean') {
    return undefined;
  }
  const confirmed = confirmedOf(value.confirmed);
  return {
    doc: normalized(value.doc),
    dropUnrecoverable: value.dropUnrecoverable,
    ...(confirmed.length > 0 ? { confirmed } : {}),
  };
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

/**
 * M14: drafts are kept per identity and project, side by side, under the one
 * key: `{ v: 2, drafts: { "<owner>|<project id or new>[|2]": draft } }`.
 * Each project (and a new, never-saved project) has at most two copies: the
 * one kept earlier and, while that one waits to be restored or dismissed, the
 * working copy of what is being typed now. Writing one copy never touches
 * another. Still this tab's session storage: browser-local, never sent to the
 * server, and not a saved revision.
 *
 * A draft in the earlier format (a single v1 draft at the key) is read as the
 * draft of its own identity and project. Anything that cannot be read is moved
 * aside to `UNREADABLE_KEY`; a draft whose identity is not the reader's is
 * moved to `QUARANTINE_KEY`. Neither is shown, offered or silently deleted.
 */
const STORE_VERSION = 2;
export const UNREADABLE_KEY = `${DRAFT_STORAGE_KEY}.unreadable`;
export const QUARANTINE_KEY = `${DRAFT_STORAGE_KEY}.quarantine`;
/**
 * At most this many quarantined entries are kept in this tab; beyond that the
 * oldest goes first. Quarantine holds only other identities' and unreadable
 * data, which signing out clears anyway.
 */
const QUARANTINE_LIMIT = 10;

/** Which copy of a project's draft: the one kept earlier, or the working copy beside it. */
export type DraftCopy = 1 | 2;
export const DRAFT_COPIES: readonly DraftCopy[] = [1, 2];

/** A kept draft, with when it was written (0 for one migrated from the earlier format). */
export type StoredDraft = DesignDraft & { savedAt: number; tenant?: string; copy: DraftCopy };

/** A kept draft as stored, with its body version (so `parseDraft` reads it as written). */
export type StoredEntry = StoredDraft & { v: number };

export function draftSlot(owner: string, project: string | null, copy: DraftCopy = 1): string {
  return `${owner}|${project ?? 'new'}${copy === 2 ? '|2' : ''}`;
}

function parseStored(value: unknown, copy: DraftCopy): StoredDraft | null {
  const draft = parseDraft(value);
  if (draft === null || !isFields(value)) return null;
  const savedAt = typeof value.savedAt === 'number' ? value.savedAt : 0;
  const tenant = typeof value.tenant === 'string' && value.tenant !== '' ? value.tenant : undefined;
  return { ...draft, savedAt, copy, ...(tenant ? { tenant } : {}) };
}

function readRaw(store: Storage, key: string): string | null {
  try {
    return store.getItem(key);
  } catch {
    return null;
  }
}

/** Keep `raw` out of sight: never shown or offered, never silently dropped. */
function quarantine(store: Storage, raw: string, reason: string): boolean {
  let list: unknown[] = [];
  try {
    const parsed: unknown = JSON.parse(readRaw(store, QUARANTINE_KEY) ?? '[]');
    if (Array.isArray(parsed)) list = parsed;
  } catch {
    list = [];
  }
  // Kept once: the same data set aside again does not push anything else out.
  if (list.some((entry) => isFields(entry) && entry.raw === raw)) return true;
  list.push({ reason, at: Date.now(), raw });
  try {
    store.setItem(QUARANTINE_KEY, JSON.stringify(list.slice(-QUARANTINE_LIMIT)));
    return true;
  } catch {
    // Over quota: the caller leaves it where it is.
    return false;
  }
}

type Loaded = {
  drafts: Record<string, StoredDraft>;
  unread: Record<string, unknown>;
  /** The whole value, when it is not a draft store at all. */
  whole?: string;
  /** Reading failed: what is stored is unknown, so nothing may be written over it. */
  unknown?: true;
  /** Each readable entry exactly as stored, so a write leaves the others byte-for-byte. */
  stored?: Record<string, unknown>;
};

/**
 * Move a whole unreadable value aside before anything is written over it:
 * to `UNREADABLE_KEY` when that is free (or holds the same), else to the
 * quarantine. False when it could not be moved: then nothing may replace it.
 */
function setAside(store: Storage, raw: string): boolean {
  const there = readRaw(store, UNREADABLE_KEY);
  if (there === raw) return true;
  if (there === null) {
    try {
      store.setItem(UNREADABLE_KEY, raw);
      return true;
    } catch {
      return false;
    }
  }
  return quarantine(store, raw, 'unreadable-value');
}

/**
 * Every kept draft by slot; an earlier single draft comes back in its own
 * slot. Entries this page cannot read are returned apart (`unread`), so a
 * write keeps them as they are. Reading changes nothing stored, except that a
 * whole value that is not a draft store is copied aside to `UNREADABLE_KEY`.
 */
function loadAll(store: Storage): Loaded {
  let raw: string | null;
  try {
    raw = store.getItem(DRAFT_STORAGE_KEY);
  } catch {
    return { drafts: {}, unread: {}, unknown: true };
  }
  if (raw === null) return { drafts: {}, unread: {} };
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    value = undefined;
  }
  if (isFields(value) && value.v === STORE_VERSION && isFields(value.drafts)) {
    const out: Record<string, StoredDraft> = {};
    const unread: Record<string, unknown> = {};
    const stored: Record<string, unknown> = {};
    for (const [slot, entry] of Object.entries(value.drafts)) {
      const copy: DraftCopy = slot.endsWith('|2') ? 2 : 1;
      const draft = parseStored({ ...(isFields(entry) ? entry : {}), v: DRAFT_VERSION }, copy);
      if (draft !== null && slot === draftSlot(draft.session, draft.project?.id ?? null, copy)) {
        out[slot] = draft;
        stored[slot] = entry;
      } else {
        unread[slot] = entry;
      }
    }
    return { drafts: out, unread, stored };
  }
  const legacy = parseStored(value, 1);
  if (legacy !== null) {
    return {
      drafts: { [draftSlot(legacy.session, legacy.project?.id ?? null)]: legacy },
      unread: {},
    };
  }
  // Not a draft store this page can read: offered as nothing, changed by
  // nothing here (a write sets it aside first, see `setAside`).
  return { drafts: {}, unread: {}, whole: raw };
}

function load(store: Storage): Record<string, StoredDraft> {
  return loadAll(store).drafts;
}

/** Store `drafts`, keeping every entry this page could not read as it was. */
function save(store: Storage, drafts: Record<string, StoredDraft>, written?: string): boolean {
  const all = loadAll(store);
  const { whole } = all;
  // What is stored could not be read: writing would replace it unseen.
  if (all.unknown) return false;
  // Unreadable entries stay as they were; only the one just set aside (by
  // `keepDraft`) gives way to the copy written in its slot.
  const unread = Object.fromEntries(
    Object.entries(all.unread).filter(([slot]) => slot !== written),
  );
  // Never written over before it is set aside (storage full: nothing is written).
  if (whole !== undefined && !setAside(store, whole)) return false;
  try {
    if (Object.keys(drafts).length === 0 && Object.keys(unread).length === 0) {
      store.removeItem(DRAFT_STORAGE_KEY);
    } else {
      const body: Record<string, unknown> = { ...unread };
      for (const [slot, { copy: _copy, ...draft }] of Object.entries(drafts)) {
        // Any copy not being written now goes back exactly as it was stored.
        body[slot] =
          slot !== written && all.stored !== undefined && slot in all.stored
            ? all.stored[slot]
            : draft;
      }
      store.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ v: STORE_VERSION, drafts: body }));
    }
    return true;
  } catch {
    // Over quota or blocked: the caller says the work is not kept.
    return false;
  }
}

/**
 * The outcome of keeping a draft: `kept`; `failed` (storage blocked or full);
 * or `blocked`: its slot holds a copy this page cannot read, which could not
 * be set aside, so nothing is written over it.
 */
export type KeepOutcome = 'kept' | 'failed' | 'blocked';

/**
 * Keep `draft` in its own slot (its identity, project and copy), leaving every
 * other draft as it is. A copy this page cannot read in that very slot is set
 * aside first (quarantine); when that fails the slot is not written (M14).
 */
export function keepDraft(
  draft: DesignDraft,
  tenant?: string | null,
  copy: DraftCopy = 1,
): KeepOutcome {
  const store = storage();
  if (store === null) return 'failed';
  const slot = draftSlot(draft.session, draft.project?.id ?? null, copy);
  const { drafts, unread, unknown } = loadAll(store);
  if (unknown) return 'failed';
  if (slot in unread) {
    if (!quarantine(store, JSON.stringify({ slot, entry: unread[slot] }), 'unreadable-entry')) {
      return 'blocked';
    }
  }
  drafts[slot] = {
    ...draft,
    savedAt: Date.now(),
    copy,
    ...(tenant ? { tenant } : {}),
  };
  return save(store, drafts, slot) ? 'kept' : 'failed';
}

/** `keepDraft`, as true when kept. */
export function writeDraft(
  draft: DesignDraft,
  tenant?: string | null,
  copy: DraftCopy = 1,
): boolean {
  return keepDraft(draft, tenant, copy) === 'kept';
}

/** The slots of `owner` that hold a copy this page cannot read (contents never returned). */
export function unreadableCopies(owner: string): { project: string | null; copy: DraftCopy }[] {
  const store = storage();
  if (store === null) return [];
  return Object.keys(loadAll(store).unread).flatMap((slot) => {
    if (!slot.startsWith(`${owner}|`)) return [];
    const rest = slot.slice(owner.length + 1);
    const copy: DraftCopy = rest.endsWith('|2') ? 2 : 1;
    const id = copy === 2 ? rest.slice(0, -2) : rest;
    return [{ project: id === 'new' ? null : id, copy }];
  });
}

/**
 * Delete, at the engineer's explicit request, the unreadable copy in one slot
 * of `owner` (its own: another identity's slot is never touched). Every other
 * copy stays byte-for-byte. False when it could not be deleted.
 */
export function deleteUnreadableCopy(
  owner: string,
  project: string | null,
  copy: DraftCopy,
): boolean {
  const store = storage();
  if (store === null) return false;
  const slot = draftSlot(owner, project, copy);
  const raw = readRaw(store, DRAFT_STORAGE_KEY);
  if (raw === null) return false;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return false;
  }
  if (!isFields(value) || value.v !== STORE_VERSION || !isFields(value.drafts)) return false;
  const { unread } = loadAll(store);
  if (!(slot in unread)) return false;
  const rest = Object.fromEntries(Object.entries(value.drafts).filter(([key]) => key !== slot));
  try {
    if (Object.keys(rest).length === 0) store.removeItem(DRAFT_STORAGE_KEY);
    else store.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ v: STORE_VERSION, drafts: rest }));
    return true;
  } catch {
    return false;
  }
}

/** How many items are set aside in this tab (quarantine, unreadable slot); contents never returned. */
export function setAsideCount(): number {
  const store = storage();
  if (store === null) return 0;
  let count = readRaw(store, UNREADABLE_KEY) === null ? 0 : 1;
  try {
    const list: unknown = JSON.parse(readRaw(store, QUARANTINE_KEY) ?? '[]');
    if (Array.isArray(list)) count += list.length;
  } catch {
    count += 1;
  }
  return count;
}

/** Delete, at the engineer's explicit request, everything set aside in this tab, unread. */
export function clearSetAside(): boolean {
  const store = storage();
  if (store === null) return false;
  try {
    store.removeItem(QUARANTINE_KEY);
    store.removeItem(UNREADABLE_KEY);
    return true;
  } catch {
    return false;
  }
}

/**
 * Remove kept drafts: with no arguments, every one, quarantined and unreadable
 * data included (signing out, another account: an explicit end of the work); with an owner, that identity's; with a project too, that project's
 * copies (`null`: the project never saved); with a copy too, only that copy.
 */
export function clearDraft(owner?: string, project?: string | null, copy?: DraftCopy): void {
  const store = storage();
  if (store === null) return;
  if (owner === undefined) {
    // Signing out or another account: nothing typed here stays, set aside or not.
    try {
      store.removeItem(DRAFT_STORAGE_KEY);
      store.removeItem(QUARANTINE_KEY);
      store.removeItem(UNREADABLE_KEY);
    } catch {
      // Nothing stored can be reached, so nothing is left to offer either.
    }
    return;
  }
  const gone = (slot: string) => {
    if (project === undefined) return slot.startsWith(`${owner}|`);
    if (copy !== undefined) return slot === draftSlot(owner, project, copy);
    return DRAFT_COPIES.some((each) => slot === draftSlot(owner, project, each));
  };
  save(store, Object.fromEntries(Object.entries(load(store)).filter(([slot]) => !gone(slot))));
}

/** Every kept draft, most recently written first (for tests and the arrival offer). */
export function storedDrafts(): StoredEntry[] {
  const store = storage();
  if (store === null) return [];
  return Object.values(load(store))
    .sort((a, b) => b.savedAt - a.savedAt)
    .map((draft) => ({ v: DRAFT_VERSION, ...draft }));
}

/**
 * Settle what is kept for `session`, once (on arrival, before anything is
 * offered or written): another identity's drafts and entries that cannot be
 * read are moved to quarantine. Each leaves the store only once its copy in
 * quarantine is written; when that fails (storage full) it stays where it is,
 * never offered. Never shown, never silently deleted.
 */
export function settleDrafts(session: string): void {
  const store = storage();
  if (store === null) return;
  const { drafts, unread, whole } = loadAll(store);
  if (whole !== undefined) {
    setAside(store, whole);
    return;
  }
  const kept: Record<string, StoredDraft> = {};
  let moved = false;
  for (const [slot, draft] of Object.entries(drafts)) {
    if (draft.session === session) kept[slot] = draft;
    else if (quarantine(store, JSON.stringify({ slot, ...draft }), 'other-identity')) moved = true;
    else kept[slot] = draft;
  }
  const stillUnread: Record<string, unknown> = {};
  for (const [slot, entry] of Object.entries(unread)) {
    if (quarantine(store, JSON.stringify({ slot, entry }), 'unreadable-entry')) moved = true;
    else stillUnread[slot] = entry;
  }
  if (!moved) return;
  try {
    const body: Record<string, unknown> = { ...stillUnread };
    for (const [slot, { copy: _copy, ...draft }] of Object.entries(kept)) body[slot] = draft;
    if (Object.keys(body).length === 0) store.removeItem(DRAFT_STORAGE_KEY);
    else store.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ v: STORE_VERSION, drafts: body }));
  } catch {
    // Not rewritten: everything stays where it was (and is in quarantine too).
  }
}

/**
 * The drafts `session` may be offered, most recent first: its own, under the
 * tenant `tenant` when both are known; with `project` given, only that
 * project's copies. Reads only: another identity's drafts are left out, never
 * offered (`settleDrafts` moves them to quarantine).
 */
export function ownDrafts(
  session: string,
  tenant?: string | null,
  project?: string | null,
): StoredDraft[] {
  const store = storage();
  if (store === null) return [];
  return Object.values(load(store))
    .filter(
      (draft) =>
        draft.session === session &&
        (tenant == null || draft.tenant === undefined || draft.tenant === tenant) &&
        (project === undefined || (draft.project?.id ?? null) === project),
    )
    .sort((a, b) => b.savedAt - a.savedAt);
}

/**
 * The draft typed by `session` for `project` (`null`: the project never
 * saved), or with `project` left out its most recent one; null when none.
 * See `ownDrafts` for what is offered and what is quarantined.
 */
export function readDraft(
  session: string,
  project?: string | null,
  tenant?: string | null,
): DesignDraft | null {
  settleDrafts(session);
  const found = ownDrafts(session, tenant, project)[0];
  if (found === undefined) return null;
  const { savedAt: _savedAt, tenant: _tenant, copy: _copy, ...draft } = found;
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
