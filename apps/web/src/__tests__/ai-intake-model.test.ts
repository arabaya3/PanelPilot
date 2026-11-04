import { describe, expect, it, vi } from 'vitest';

import { decided, DESCRIPTION, proposal } from './ai-intake-fixtures';

import {
  aiRequirementsInput,
  confirmedValue,
  editDraftOf,
  isProblem,
  placedItems,
  placementsOf,
  placeItem,
  spanParts,
  statusOf,
  untagged,
  validateEdit,
  type Placed,
} from '@/components/design/ai-intake-model';
import {
  draftControlTags,
  formControlTags,
  parseDraft,
  type DesignDraft,
} from '@/components/design/draft';
import { blankBoard, IEC_SUPPLY, type BoardForm } from '@/components/design/schedule';
import {
  AI_DESCRIPTION_LIMIT,
  CONTROL_NEEDS,
  decideAiItem,
  getAiProposal,
  requestAiProposal,
  type AiItem,
} from '@/lib/design';

/** M6b: the proposal model, the placement rules and the API calls, without a page. */

function keys(start = 100) {
  let next = start;
  return (count: number) => {
    const first = next;
    next += Math.max(1, count);
    return first;
  };
}

const item = (id: string): AiItem => {
  const found = proposal().items.find((entry) => entry.id === id);
  if (!found) throw new Error(id);
  return found;
};

function placed(result: ReturnType<typeof placeItem>): Placed {
  if (isProblem(result)) throw new Error(`not placed: ${result.code}`);
  return result;
}

describe('proposal status and value', () => {
  it('is proposed until the server records a decision, and the latest decision stands', () => {
    let p = proposal();
    expect(statusOf(p, 'i-pump')).toBe('proposed');
    p = decided(p, 'i-pump', 'accept');
    expect(statusOf(p, 'i-pump')).toBe('accepted');
    p = decided(p, 'i-pump', 'edit', { ...item('i-pump').value, power_kw: 5.5 });
    expect(statusOf(p, 'i-pump')).toBe('edited');
    expect(confirmedValue(p, item('i-pump')).power_kw).toBe(5.5);
    p = decided(p, 'i-pump', 'reject');
    expect(statusOf(p, 'i-pump')).toBe('rejected');
    expect(confirmedValue(p, item('i-pump')).power_kw).toBe(7.5);
  });
});

describe('source spans', () => {
  it('quote the span of the original description', () => {
    const { source } = item('i-pump');
    expect(spanParts(DESCRIPTION, source.start, source.end)?.span).toBe(
      'Pump P1 7.5 kW three phase',
    );
  });

  it('count offsets in code points, as the server does', () => {
    const text = '😀 لوحة MDB';
    expect(spanParts(text, 2, 6)?.span).toBe('لوحة');
    expect(spanParts(text, 0, 1)?.span).toBe('😀');
  });

  it('give no span for offsets outside the text', () => {
    expect(spanParts('abc', 1, 9)).toBeNull();
    expect(spanParts('abc', -1, 2)).toBeNull();
    expect(spanParts('abc', 2, 2)).toBeNull();
  });
});

describe('edit validation', () => {
  it('starts from what the item gives and nothing more', () => {
    expect(editDraftOf('load', item('i-light').value)).toEqual({
      board: 'MDB',
      description: 'Lighting',
      kind: 'lighting',
      power_kw: '',
      phases: '',
      power_factor: '',
      length_m: '',
      controlled: '',
    });
  });

  it('leaves empty optional fields out, never sending a default', () => {
    const result = validateEdit('load', {
      board: 'MDB',
      description: 'Lighting',
      kind: 'lighting',
      phases: '1',
    });
    expect(result).toEqual({
      value: { board: 'MDB', description: 'Lighting', kind: 'lighting', phases: 1 },
    });
  });

  it('refuses each field at fault', () => {
    const result = validateEdit('load', {
      board: '',
      description: 'x'.repeat(201),
      kind: 'sub_board',
      power_kw: '-1',
      power_factor: '1.2',
      length_m: 'abc',
      phases: '2',
    });
    expect(result).toEqual({
      errors: {
        board: 'required',
        description: 'tooLong',
        kind: 'kind',
        power_kw: 'number',
        power_factor: 'powerFactor',
        length_m: 'number',
        phases: 'phases',
      },
    });
    expect(validateEdit('board', { name: 'MDB', fed_from: 'MDB' })).toEqual({
      errors: { fed_from: 'sameBoard' },
    });
    expect(validateEdit('control_need', { need: 'make_coffee' })).toEqual({
      errors: { need: 'need' },
    });
    expect(validateEdit('control_need', { need: 'timed' })).toEqual({ value: { need: 'timed' } });
  });
});

describe('placing confirmed items in the editor', () => {
  const start = (): Placed => ({
    boards: [blankBoard(0, 1, 'DB1')],
    control: {},
    controlTags: {},
  });

  it('adds a board tagged with its item, voltage left empty and no rows invented', () => {
    const p = decided(proposal(), 'i-board', 'accept');
    const result = placed(placeItem(start(), p, item('i-board'), keys(), IEC_SUPPLY));
    const board = result.boards[1];
    expect(board).toMatchObject({ name: 'MDB', aiItem: 'i-board', voltage: '', fedFrom: '' });
    expect(board?.loads).toEqual([]);
  });

  it('adds a load with only the fields the item gives', () => {
    let p = decided(proposal(), 'i-board', 'accept');
    let state = placed(placeItem(start(), p, item('i-board'), keys(), IEC_SUPPLY));
    p = decided(p, 'i-pump', 'accept');
    state = placed(placeItem(state, p, item('i-pump'), keys(200), IEC_SUPPLY));
    expect(state.boards[1]?.loads).toEqual([
      {
        key: 200,
        description: 'Pump P1',
        load: 'motor',
        power: '7.5',
        phases: '3',
        powerFactor: '',
        controlled: false,
        starter: '',
        length: '',
        aiItem: 'i-pump',
      },
    ]);
  });

  it('refuses a load whose phases are not given, until an edit gives them', () => {
    let p = decided(proposal(), 'i-board', 'accept');
    const state = placed(placeItem(start(), p, item('i-board'), keys(), IEC_SUPPLY));
    p = decided(p, 'i-light', 'accept');
    expect(placeItem(state, p, item('i-light'), keys(200), IEC_SUPPLY)).toEqual({
      code: 'missing',
      fields: ['phases'],
    });
    p = decided(p, 'i-light', 'edit', { ...item('i-light').value, phases: 1 });
    const after = placed(placeItem(state, p, item('i-light'), keys(200), IEC_SUPPLY));
    expect(after.boards[1]?.loads[0]).toMatchObject({ phases: '1', power: '', aiItem: 'i-light' });
  });

  it('refuses a load for a board the editor does not hold, and a second board of one name', () => {
    const p = decided(decided(proposal(), 'i-pump', 'accept'), 'i-board', 'accept');
    expect(placeItem(start(), p, item('i-pump'), keys(), IEC_SUPPLY)).toEqual({
      code: 'boardNotInEditor',
      board: 'MDB',
    });
    const twice: Placed = { ...start(), boards: [blankBoard(0, 1, 'MDB')] };
    expect(placeItem(twice, p, item('i-board'), keys(), IEC_SUPPLY)).toEqual({
      code: 'boardExists',
      board: 'MDB',
    });
  });

  it('ticks a control need on its placed load, setting "PLC switched" as the item implies', () => {
    let p = decided(proposal(), 'i-board', 'accept');
    let state = placed(placeItem(start(), p, item('i-board'), keys(), IEC_SUPPLY));
    p = decided(decided(p, 'i-pump', 'accept'), 'i-start', 'accept');
    expect(placeItem(state, p, item('i-start'), keys(200), IEC_SUPPLY)).toEqual({
      code: 'loadNotPlaced',
    });
    state = placed(placeItem(state, p, item('i-pump'), keys(200), IEC_SUPPLY));
    state = placed(placeItem(state, p, item('i-start'), keys(300), IEC_SUPPLY));
    expect(state.boards[1]?.loads[0]?.controlled).toBe(true);
    expect(state.control[200]).toEqual(['start_stop']);
    expect(state.controlTags['i-start']).toEqual({ loadKey: 200, need: 'start_stop' });
  });

  it('never places a question or unsupported text', () => {
    const p = decided(proposal(), 'i-q', 'accept');
    expect(placeItem(start(), p, item('i-q'), keys(), IEC_SUPPLY)).toEqual({
      code: 'notPlaceable',
    });
  });
});

describe('placements a save sends', () => {
  function placedForm(): Placed {
    let p = decided(proposal(), 'i-board', 'accept');
    let state: Placed = { boards: [blankBoard(0, 1, 'DB1')], control: {}, controlTags: {} };
    state = placed(placeItem(state, p, item('i-board'), keys(), IEC_SUPPLY));
    p = decided(decided(p, 'i-pump', 'accept'), 'i-start', 'accept');
    state = placed(placeItem(state, p, item('i-pump'), keys(200), IEC_SUPPLY));
    return placed(placeItem(state, p, item('i-start'), keys(300), IEC_SUPPLY));
  }

  it('name each placed item by board and row position', () => {
    const state = placedForm();
    expect(placementsOf(state.boards, state.control, state.controlTags)).toEqual([
      { item_id: 'i-board', board_index: 1 },
      { item_id: 'i-pump', board_index: 1, load_index: 0 },
      { item_id: 'i-start', board_index: 1, load_index: 0 },
    ]);
  });

  it('keep the tag through an edit of the row', () => {
    const state = placedForm();
    const boards: BoardForm[] = state.boards.map((board) => ({
      ...board,
      loads: board.loads.map((load) => ({ ...load, description: 'Pump P1 (duty)', power: '9' })),
    }));
    expect(placedItems(boards, state.control, state.controlTags).has('i-pump')).toBe(true);
  });

  it('drop a removed row, and the control need on it', () => {
    const state = placedForm();
    const boards = state.boards.map((board) => ({ ...board, loads: [] }));
    expect(placementsOf(boards, state.control, state.controlTags)).toEqual([
      { item_id: 'i-board', board_index: 1 },
    ]);
    // An unticked need is no placement either.
    expect(placementsOf(state.boards, { 200: [] }, state.controlTags)).toHaveLength(2);
  });

  it('send intake "ai" with the proposal id, even with nothing placed', () => {
    const state = placedForm();
    const base = { intake: 'guided' as const, control: [] };
    expect(aiRequirementsInput(base, [], {}, {}, proposal())).toEqual({
      intake: 'ai',
      control: [],
      ai: { proposal_id: 'prop-1', placements: [] },
    });
    expect(
      aiRequirementsInput(base, state.boards, state.control, state.controlTags, proposal()).ai
        ?.placements,
    ).toHaveLength(3);
  });

  it('come off every row after a save', () => {
    const rows = untagged(placedForm().boards);
    expect(JSON.stringify(rows)).not.toContain('aiItem');
  });
});

describe('the unsaved-work draft', () => {
  it('keeps the description, the proposal id, the row tags and the control tags', () => {
    const state = (() => {
      let p = decided(proposal(), 'i-board', 'accept');
      let s: Placed = { boards: [blankBoard(0, 1, 'DB1')], control: {}, controlTags: {} };
      s = placed(placeItem(s, p, item('i-board'), keys(), IEC_SUPPLY));
      p = decided(decided(p, 'i-pump', 'accept'), 'i-start', 'accept');
      s = placed(placeItem(s, p, item('i-pump'), keys(200), IEC_SUPPLY));
      return placed(placeItem(s, p, item('i-start'), keys(300), IEC_SUPPLY));
    })();
    const tags = draftControlTags(state.boards, state.controlTags);
    expect(tags).toEqual([{ item: 'i-start', board: 1, row: 0, need: 'start_stop' }]);
    const draft = {
      v: 1,
      session: 's',
      project: null,
      clean: '',
      info: { name: '', number: '', customer: '', consultant: '', contractor: '' },
      boards: state.boards.map(({ key: _k, loads, ...board }) => ({
        ...board,
        loads: loads.map(({ key: _l, ...load }) => load),
      })),
      profileText: '',
      language: null,
      market: '',
      ai: { description: DESCRIPTION, proposalId: 'prop-1', controlTags: tags },
    };
    const parsed = parseDraft(JSON.parse(JSON.stringify(draft))) as DesignDraft;
    expect(parsed.ai).toEqual({
      description: DESCRIPTION,
      proposalId: 'prop-1',
      controlTags: tags,
    });
    expect(parsed.boards[1]?.aiItem).toBe('i-board');
    expect(parsed.boards[1]?.loads[0]?.aiItem).toBe('i-pump');
    const restored = state.boards.map((board, b) => ({
      ...board,
      loads: board.loads.map((load) => ({ ...load, key: 900 + b })),
    }));
    expect(formControlTags(restored, parsed.ai?.controlTags)).toEqual({
      'i-start': { loadKey: 901, need: 'start_stop' },
    });
  });
});

describe('the AI API calls', () => {
  function respond(status: number, body?: unknown) {
    return vi.fn<typeof fetch>().mockResolvedValue(
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }

  it('lists the seven control needs the API records', () => {
    expect(CONTROL_NEEDS).toHaveLength(7);
    expect(AI_DESCRIPTION_LIMIT).toBe(8000);
  });

  it('posts only the typed description and the current request', async () => {
    const fetchImpl = respond(201, proposal());
    const outcome = await requestAiProposal({ token: 't', fetchImpl, description: 'text' });
    expect(outcome).toEqual({ kind: 'proposed', proposal: proposal() });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/design/projects/requirements/ai-proposals');
    expect(JSON.parse(init?.body as string)).toEqual({ description: 'text' });
  });

  it.each([
    [503, { code: 'ai_unavailable', detail: 'x' }, 'ai_unavailable'],
    [503, undefined, 'ai_unavailable'],
    [502, { code: 'ai_proposal_malformed' }, 'ai_proposal_malformed'],
    [502, undefined, 'ai_proposal_malformed'],
    [504, undefined, 'ai_timeout'],
    [422, { code: 'ai_input_too_long' }, 'ai_input_too_long'],
    [422, { code: 'ai_input_empty' }, 'ai_input_empty'],
    [403, { code: 'ai_requires_account' }, 'ai_requires_account'],
    [409, { code: 'ai_proposal_used' }, 'ai_proposal_used'],
  ])('says a %i as its code', async (status, body, code) => {
    const outcome = await requestAiProposal({
      token: 't',
      fetchImpl: respond(status, body),
      description: 'text',
    });
    expect(outcome).toMatchObject({ kind: 'refused', code });
  });

  it('refuses a proposal body without its items as failed', async () => {
    const outcome = await requestAiProposal({
      token: 't',
      fetchImpl: respond(201, { id: 'x' }),
      description: 'text',
    });
    expect(outcome).toEqual({ kind: 'failed' });
  });

  it('sends a decision with no who or when, and an edit with its value', async () => {
    const fetchImpl = respond(200, decided(proposal(), 'i-pump', 'accept'));
    const outcome = await decideAiItem({
      token: 't',
      fetchImpl,
      proposal: proposal(),
      item: 'i-pump',
      decision: 'accept',
    });
    expect(outcome.kind).toBe('proposed');
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(
      '/api/v1/design/projects/requirements/ai-proposals/prop-1/items/i-pump/decision',
    );
    expect(JSON.parse(init?.body as string)).toEqual({ decision: 'accept' });

    const edit = respond(200, proposal());
    await decideAiItem({
      token: 't',
      fetchImpl: edit,
      proposal: proposal(),
      item: 'i-light',
      decision: 'edit',
      value: { phases: 1 },
    });
    expect(JSON.parse(edit.mock.calls[0]?.[1]?.body as string)).toEqual({
      decision: 'edit',
      value: { phases: 1 },
    });
  });

  it('takes a bare decision record back onto the proposal', async () => {
    const record = {
      item_id: 'i-pump',
      decision: 'reject',
      by_user_id: 'u',
      by_name: 'Dana',
      at: '2026-11-04T10:00:00Z',
    };
    const outcome = await decideAiItem({
      token: 't',
      fetchImpl: respond(201, record),
      proposal: proposal(),
      item: 'i-pump',
      decision: 'reject',
    });
    expect(outcome.kind === 'proposed' && outcome.proposal.decisions).toEqual([record]);
  });

  it('reads a proposal again by its id', async () => {
    const fetchImpl = respond(200, proposal());
    const outcome = await getAiProposal({ token: 't', fetchImpl, id: 'prop-1' });
    expect(outcome.kind).toBe('proposed');
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      '/api/v1/design/projects/requirements/ai-proposals/prop-1',
    );
    expect(fetchImpl.mock.calls[0]?.[1]?.method).toBe('GET');
  });
});
