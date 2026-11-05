import {
  blankBoard,
  NEW_LOAD_KINDS,
  type BoardForm,
  type Load,
  type SupplyDefaults,
} from '@/components/design/schedule';
import {
  CONTROL_NEEDS,
  type AiDecision,
  type AiItem,
  type AiPlacement,
  type AiProposal,
  type ControlNeed,
  type LoadKind,
  type RequirementsInput,
} from '@/lib/design';

/**
 * M6b: what the page does with an AI proposal, apart from showing it.
 *
 * The rule throughout: the page never fills in a value that neither the
 * engineer nor a confirmed item gave. A load the item does not give phases
 * for cannot be placed until an edit gives them; nothing defaults a power,
 * a power factor or a length.
 */

export type AiStatus = 'proposed' | 'accepted' | 'edited' | 'rejected';

/** A control need placed from an item: on which row, which need. */
export type AiControlTag = { loadKey: number; need: ControlNeed };
export type AiControlTags = Readonly<Record<string, AiControlTag>>;

/** The latest decision on an item (decisions are append-only; the last one stands). */
export function latestDecision(proposal: AiProposal, itemId: string): AiDecision | null {
  for (let index = proposal.decisions.length - 1; index >= 0; index--) {
    const decision = proposal.decisions[index];
    if (decision?.item_id === itemId) return decision;
  }
  return null;
}

export function statusOf(proposal: AiProposal, itemId: string): AiStatus {
  const decision = latestDecision(proposal, itemId);
  if (decision === null) return 'proposed';
  return decision.decision === 'accept'
    ? 'accepted'
    : decision.decision === 'edit'
      ? 'edited'
      : 'rejected';
}

/** The value an item stands at: its edit where the last decision is one, else as proposed. */
export function confirmedValue(proposal: AiProposal, item: AiItem): Record<string, unknown> {
  const decision = latestDecision(proposal, item.id);
  return decision?.decision === 'edit' && decision.value ? decision.value : item.value;
}

/**
 * The text before, inside and after a source span. The server counts
 * offsets in code points (Python string indices), so a character outside
 * the BMP is one position, not two UTF-16 units. Out-of-range offsets give
 * no span rather than a wrong one.
 */
export function spanParts(
  text: string,
  start: number,
  end: number,
): { before: string; span: string; after: string } | null {
  const chars = Array.from(text);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > chars.length) {
    return null;
  }
  if (end <= start) return null;
  return {
    before: chars.slice(0, start).join(''),
    span: chars.slice(start, end).join(''),
    after: chars.slice(end).join(''),
  };
}

/** A proposed field as a string, or '' where the item does not set it. */
export function text(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return '';
}

/** The fields each kind's edit form offers: only what the contract lets that kind set. */
export const EDIT_FIELDS = {
  board: ['name', 'fed_from'],
  load: [
    'board',
    'description',
    'kind',
    'power_kw',
    'phases',
    'power_factor',
    'length_m',
    'controlled',
  ],
  control_need: ['need'],
} as const;

export type EditableKind = keyof typeof EDIT_FIELDS;
export type EditDraft = Record<string, string>;

export function isEditable(kind: string): kind is EditableKind {
  return kind in EDIT_FIELDS;
}

/** The edit form's starting values: what the item (as it stands) gives, nothing more. */
export function editDraftOf(kind: EditableKind, value: Record<string, unknown>): EditDraft {
  const draft: EditDraft = {};
  for (const field of EDIT_FIELDS[kind]) {
    const given = value[field];
    draft[field] =
      field === 'controlled'
        ? given === true
          ? 'true'
          : given === false
            ? 'false'
            : ''
        : text(given);
  }
  return draft;
}

export type EditError =
  | 'required'
  | 'tooLong'
  | 'number'
  | 'positive'
  | 'powerFactor'
  | 'phases'
  | 'kind'
  | 'need'
  | 'sameBoard';

/** The longest proposed string the server keeps. */
const TEXT_LIMIT = 200;

function positive(value: string): number | EditError {
  const number = Number(value);
  if (!/^\d+(\.\d+)?$/.test(value.trim()) || !Number.isFinite(number)) return 'number';
  return number > 0 ? number : 'positive';
}

/**
 * An edit form checked against its kind's fields: the value to send, or the
 * error for each field at fault. An empty optional field is left out, never
 * sent as a default.
 */
export function validateEdit(
  kind: EditableKind,
  draft: EditDraft,
): { value: Record<string, unknown> } | { errors: Record<string, EditError> } {
  const errors: Record<string, EditError> = {};
  const value: Record<string, unknown> = {};
  const get = (field: string) => (draft[field] ?? '').trim();
  const required = (field: string) => {
    const given = get(field);
    if (given === '') errors[field] = 'required';
    else if (given.length > TEXT_LIMIT) errors[field] = 'tooLong';
    else value[field] = given;
  };
  if (kind === 'board') {
    required('name');
    const from = get('fed_from');
    if (from.length > TEXT_LIMIT) errors.fed_from = 'tooLong';
    else if (from !== '' && from === get('name')) errors.fed_from = 'sameBoard';
    else if (from !== '') value.fed_from = from;
  } else if (kind === 'control_need') {
    const need = get('need');
    if (CONTROL_NEEDS.some((entry) => entry === need)) value.need = need;
    else errors.need = need === '' ? 'required' : 'need';
  } else {
    required('board');
    required('description');
    const kindOf = get('kind');
    if (kindOf === '') errors.kind = 'required';
    else if (!NEW_LOAD_KINDS.some((entry) => entry === kindOf)) errors.kind = 'kind';
    else value.kind = kindOf;
    const phases = get('phases');
    if (phases === '1' || phases === '3') value.phases = Number(phases);
    else if (phases !== '') errors.phases = 'phases';
    for (const field of ['power_kw', 'length_m'] as const) {
      const given = get(field);
      if (given === '') continue;
      const number = positive(given);
      if (typeof number === 'number') value[field] = number;
      else errors[field] = number;
    }
    const pf = get('power_factor');
    if (pf !== '') {
      const number = positive(pf);
      if (typeof number !== 'number') errors.power_factor = number;
      else if (number > 1) errors.power_factor = 'powerFactor';
      else value.power_factor = number;
    }
    const controlled = get('controlled');
    if (controlled === 'true') value.controlled = true;
    else if (controlled === 'false') value.controlled = false;
  }
  return Object.keys(errors).length > 0 ? { errors } : { value };
}

/** Why an item cannot be placed in the editor as it stands. */
export type PlaceProblem =
  | { code: 'missing'; fields: string[] }
  | { code: 'boardNotInEditor'; board: string }
  | { code: 'boardExists'; board: string }
  | { code: 'loadNotPlaced' }
  | { code: 'notPlaceable' };

/** The fields a load must have before it can be a row (the editor has no "unknown" for them). */
export function loadMissing(value: Record<string, unknown>): string[] {
  const missing: string[] = [];
  if (text(value.board).trim() === '') missing.push('board');
  if (text(value.description).trim() === '') missing.push('description');
  if (!NEW_LOAD_KINDS.some((kind) => kind === value.kind)) missing.push('kind');
  if (text(value.phases) !== '1' && text(value.phases) !== '3') missing.push('phases');
  return missing;
}

/** The item ids the editor holds now: tagged boards and rows, and control needs still ticked. */
export function placedItems(
  boards: readonly BoardForm[],
  control: Readonly<Record<number, readonly ControlNeed[]>>,
  controlTags: AiControlTags,
): Set<string> {
  return new Set(placementsOf(boards, control, controlTags).map((entry) => entry.item_id));
}

/** Where each placed item sits, by board and row position, as the save sends it. */
export function placementsOf(
  boards: readonly BoardForm[],
  control: Readonly<Record<number, readonly ControlNeed[]>>,
  controlTags: AiControlTags,
): AiPlacement[] {
  const placements: AiPlacement[] = [];
  const rowAt = new Map<number, { board_index: number; load_index: number }>();
  boards.forEach((board, boardIndex) => {
    if (board.aiItem) placements.push({ item_id: board.aiItem, board_index: boardIndex });
    board.loads.forEach((load, loadIndex) => {
      rowAt.set(load.key, { board_index: boardIndex, load_index: loadIndex });
      if (load.aiItem) {
        placements.push({ item_id: load.aiItem, board_index: boardIndex, load_index: loadIndex });
      }
    });
  });
  for (const [itemId, tag] of Object.entries(controlTags)) {
    const at = rowAt.get(tag.loadKey);
    if (at && control[tag.loadKey]?.includes(tag.need)) placements.push({ item_id: itemId, ...at });
  }
  return placements;
}

/**
 * What a save records with an unused proposal open: `intake: "ai"`, the
 * control needs, and the proposal with its placements (none placed is still
 * sent, so the undecided items, questions and unsupported text are recorded
 * as open items).
 */
export function aiRequirementsInput(
  base: RequirementsInput,
  boards: readonly BoardForm[],
  control: Readonly<Record<number, readonly ControlNeed[]>>,
  controlTags: AiControlTags,
  proposal: AiProposal,
): RequirementsInput {
  return {
    ...base,
    intake: 'ai',
    ai: { proposal_id: proposal.id, placements: placementsOf(boards, control, controlTags) },
  };
}

/** The tags back off every row: after a save, a used proposal can place nothing more. */
export function untagged(boards: readonly BoardForm[]): BoardForm[] {
  return boards.map(({ aiItem: _board, loads, ...board }) => ({
    ...board,
    loads: loads.map(({ aiItem: _load, ...load }) => load),
  }));
}

export type Placed = {
  boards: BoardForm[];
  control: Record<number, ControlNeed[]>;
  controlTags: Record<string, AiControlTag>;
};

/**
 * A confirmed item placed in the editor, tagged with its id, or why it cannot
 * be. `takeKeys(n)` hands out unused row keys. A board takes the editor's
 * blank-board supply with the voltage left empty (the description gives no
 * supply, so the engineer sets it); a load takes only the fields its value
 * gives.
 */
export function placeItem(
  current: {
    boards: readonly BoardForm[];
    control: Readonly<Record<number, readonly ControlNeed[]>>;
    controlTags: AiControlTags;
  },
  proposal: AiProposal,
  item: AiItem,
  takeKeys: (count: number) => number,
  supply: SupplyDefaults,
): Placed | PlaceProblem {
  const value = confirmedValue(proposal, item);
  const boards = [...current.boards];
  const control: Record<number, ControlNeed[]> = Object.fromEntries(
    Object.entries(current.control).map(([key, needs]) => [key, [...needs]]),
  );
  const controlTags: Record<string, AiControlTag> = { ...current.controlTags };
  const findBoard = (name: string) => boards.findIndex((board) => board.name.trim() === name);

  if (item.kind === 'board') {
    const name = text(value.name).trim();
    if (name === '') return { code: 'missing', fields: ['name'] };
    if (findBoard(name) >= 0) return { code: 'boardExists', board: name };
    const from = text(value.fed_from).trim();
    if (from !== '' && findBoard(from) < 0) return { code: 'boardNotInEditor', board: from };
    const key = takeKeys(1);
    const board = blankBoard(key, key, name, from, supply);
    boards.push({ ...board, voltage: '', loads: [], aiItem: item.id });
    return { boards, control, controlTags };
  }

  if (item.kind === 'load') {
    const missing = loadMissing(value);
    if (missing.length > 0) return { code: 'missing', fields: missing };
    const boardName = text(value.board).trim();
    const at = findBoard(boardName);
    const target = boards[at];
    if (target === undefined) return { code: 'boardNotInEditor', board: boardName };
    const load: Load = {
      key: takeKeys(1),
      description: text(value.description).trim(),
      load: value.kind as LoadKind,
      power: text(value.power_kw),
      phases: text(value.phases) === '3' ? '3' : '1',
      powerFactor: text(value.power_factor),
      controlled: value.controlled === true,
      starter: '',
      length: text(value.length_m),
      aiItem: item.id,
    };
    boards[at] = { ...target, loads: [...target.loads, load] };
    return { boards, control, controlTags };
  }

  if (item.kind === 'control_need') {
    const need = CONTROL_NEEDS.find((entry) => entry === value.need);
    if (need === undefined) return { code: 'missing', fields: ['need'] };
    const loadItem = text(value.load);
    for (let b = 0; b < boards.length; b++) {
      const board = boards[b];
      if (board === undefined) continue;
      const row = board.loads.findIndex(
        (load) => load.aiItem !== undefined && load.aiItem === loadItem,
      );
      const load = board.loads[row];
      if (load === undefined) continue;
      // A control need implies the load is PLC switched (the contract's rule).
      if (!load.controlled) {
        boards[b] = {
          ...board,
          loads: board.loads.map((entry) =>
            entry.key === load.key ? { ...entry, controlled: true } : entry,
          ),
        };
      }
      const needs = control[load.key] ?? [];
      control[load.key] = CONTROL_NEEDS.filter((entry) => entry === need || needs.includes(entry));
      controlTags[item.id] = { loadKey: load.key, need };
      return { boards, control, controlTags };
    }
    return { code: 'loadNotPlaced' };
  }

  return { code: 'notPlaceable' };
}

export function isProblem(result: Placed | PlaceProblem): result is PlaceProblem {
  return 'code' in result;
}
