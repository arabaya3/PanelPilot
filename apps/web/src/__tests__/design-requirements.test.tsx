import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderApp } from './helpers';

import { LoadRows } from '@/components/design/load-rows';
import { ProjectsPanel } from '@/components/design/projects-panel';
import {
  controlNeedsFrom,
  RequirementsView,
  requirementsInputOf,
} from '@/components/design/requirements-view';
import { blankBoard, blankLoad, type BoardForm } from '@/components/design/schedule';
import type {
  answerQuestion,
  approveRevision,
  listProjects,
  previewRequirements,
  ProjectDesignRequest,
  RequirementsRecord,
  SavedProject,
} from '@/lib/design';

/** M6a: the guided requirements step, its open items and questions, and approval integrity. */

const REQUEST = { info: { name: 'Tower' }, boards: [] } as unknown as ProjectDesignRequest;

function boards(): BoardForm[] {
  const main = blankBoard(0, 1, 'MDB');
  const sub = blankBoard(2, 3, 'DB1', 'MDB');
  return [
    {
      ...main,
      loads: [
        { ...blankLoad(1), description: 'Pump', load: 'motor', power: '7.5', controlled: true },
      ],
    },
    { ...sub, loads: [{ ...blankLoad(3), description: 'Sockets', power: '2', controlled: true }] },
  ];
}

function record(overrides: Partial<RequirementsRecord> = {}): RequirementsRecord {
  return {
    schema: 'pp-req/1',
    intake: 'guided',
    derived: false,
    feeders: [{ ref: 'b2.feeder', board_ref: 'b2', supply_board_ref: 'b1', source: 'generated' }],
    acknowledgements: [],
    boards: [
      { ref: 'b1', name: 'MDB', fed_from_ref: null },
      { ref: 'b2', name: 'DB1', fed_from_ref: 'b1' },
    ],
    loads: [
      { ref: 'b1.l1', board_ref: 'b1', description: 'Pump', source: 'typed' },
      { ref: 'b2.l1', board_ref: 'b2', description: 'Sockets', source: 'typed' },
    ],
    control: [
      {
        ref: 'b1.c1',
        board_ref: 'b1',
        load_ref: 'b1.l1',
        needs: ['start_stop'],
        status: 'not_designed',
      },
    ],
    questions: [
      {
        id: 'q-fault',
        kind: 'missing_value',
        subject: { board_ref: 'b1', load_ref: null },
        field: 'fault_level_ka',
        state: 'open',
      },
      {
        id: 'q-pf',
        kind: 'assumption',
        subject: { board_ref: 'b1', load_ref: 'b1.l1' },
        field: 'power_factor',
        finding_code: 'default_power_factor',
        state: 'open',
      },
      {
        id: 'q-flc',
        kind: 'assumption',
        subject: { board_ref: 'b1', load_ref: 'b1.l1' },
        field: 'motor_current_a',
        state: 'open',
      },
    ],
    open_items: [
      { code: 'control_not_designed', subject: { board_ref: 'b1', load_ref: 'b1.l1' } },
      { code: 'inter_board_wiring_not_modelled', subject: { board_ref: 'b2' } },
    ],
    trace: [
      { requirement_ref: 'b1.l1', design_ref: 'MDB/c1' },
      { requirement_ref: 'b2.feeder', design_ref: 'MDB/c2' },
    ],
    ...overrides,
  };
}

function savedProject(revision: number): SavedProject {
  return {
    id: 'p1',
    name: 'Tower',
    revision,
    validated: true,
    export_status: 'review_only',
    revisions: [],
    request: REQUEST,
  } as unknown as SavedProject;
}

function view(props: Partial<Parameters<typeof RequirementsView>[0]> = {}) {
  const onProject = vi.fn();
  const onBoardChange = vi.fn();
  const onControlChange = vi.fn();
  renderApp(
    <RequirementsView
      token="tok"
      boards={boards()}
      onBoardChange={onBoardChange}
      onAddBoard={vi.fn()}
      onLoadChange={vi.fn()}
      onLoadRemove={vi.fn()}
      onLoadAdd={vi.fn()}
      control={{}}
      onControlChange={onControlChange}
      request={REQUEST}
      opened={{ id: 'p1', revision: 2 }}
      savedRecord={record()}
      onProject={onProject}
      {...props}
    />,
    { locale: 'en' },
  );
  return { onProject, onBoardChange, onControlChange };
}

describe('the load kinds a row offers', () => {
  it('offers no sub-board for a new row, and says how a sub-board is added', () => {
    renderApp(
      <LoadRows
        idPrefix="t"
        loads={[blankLoad(1)]}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
      { locale: 'en' },
    );
    const options = [...screen.getByLabelText('Load type').querySelectorAll('option')].map(
      (option) => option.value,
    );
    expect(options).not.toContain('sub_board');
    expect(screen.getByTestId('design-sub-board-hint').textContent).toContain('Fed from');
  });

  it('still shows a saved sub-board row, and says the next design refuses it', () => {
    renderApp(
      <LoadRows
        idPrefix="t"
        loads={[{ ...blankLoad(1), load: 'sub_board' }]}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
      { locale: 'en' },
    );
    expect(screen.getByLabelText('Load type')).toHaveProperty('value', 'sub_board');
    expect(screen.getByTestId('design-legacy-sub-board-0').textContent).toContain('refuses');
  });
});

describe('the guided requirements step', () => {
  it('records control needs by board and load index, and reads them back', () => {
    const forms = boards();
    const input = requirementsInputOf(forms, { 3: ['running_indicator', 'start_stop'] });
    expect(input).toEqual({
      intake: 'guided',
      control: [{ board_index: 1, load_index: 0, needs: ['start_stop', 'running_indicator'] }],
    });
    expect(controlNeedsFrom(record(), forms)).toEqual({ 1: ['start_stop'] });
  });

  it('says a control need is not designed, and a sub-board’s wiring is not modelled', () => {
    const { onControlChange } = view({ control: { 1: ['start_stop'] } });
    expect(screen.getByTestId('control-row-1').textContent).toContain(
      'Control circuit not designed yet',
    );
    expect(screen.getByTestId('requirements-wiring-1').textContent).toBe(
      'Board-to-board wiring not modelled.',
    );
    fireEvent.click(screen.getByTestId('control-running_indicator-3'));
    expect(onControlChange).toHaveBeenCalledWith(3, ['running_indicator']);
  });

  it('offers a control need only on a PLC-switched load, and never ticks it for the engineer', () => {
    const forms = boards().map((board) => ({
      ...board,
      loads: board.loads.map((load) => ({ ...load, controlled: false })),
    }));
    const { onControlChange } = view({ boards: forms, control: { 1: ['start_stop'] } });
    expect(screen.getByTestId('control-start_stop-3')).toHaveProperty('disabled', true);
    // A need already given on a load no longer switched says the save will be refused, and can be cleared.
    expect(screen.getByTestId('control-not-controlled-1').textContent).toContain(
      'Not PLC switched',
    );
    expect(screen.getByTestId('control-start_stop-1')).toHaveProperty('disabled', false);
    fireEvent.click(screen.getByTestId('control-start_stop-1'));
    expect(onControlChange).toHaveBeenCalledWith(1, []);
  });

  it('says a board-level assumption, acknowledge-only', () => {
    view({
      savedRecord: record({
        questions: [
          {
            id: 'q-ac1',
            kind: 'assumption',
            subject: { board_ref: 'b1' },
            field: null,
            finding_code: 'contactor_ac1',
            answerable: false,
            state: 'open',
          },
        ],
      }),
    });
    expect(screen.getByTestId('question-q-ac1').textContent).toContain('Contactors rated for AC-1');
    expect(screen.queryByTestId('question-answer-q-ac1')).toBeNull();
    expect(screen.getByTestId('question-acknowledge-q-ac1')).toBeTruthy();
  });

  it('says the server refuses an answer on an older revision', async () => {
    const answer = vi.fn<typeof answerQuestion>().mockResolvedValue({
      kind: 'refused',
      detail: 'x',
      code: 'revision_not_latest',
      params: {},
    });
    view({ impls: { answer } });
    fireEvent.change(screen.getByLabelText('Fault level'), { target: { value: '25' } });
    fireEvent.click(screen.getByTestId('question-answer-q-fault'));
    expect((await screen.findByTestId('requirements-message')).textContent).toContain(
      'latest revision only',
    );
  });

  it('lists the open items for control and for board-to-board wiring', () => {
    view();
    expect(screen.getByTestId('open-item-control_not_designed').textContent).toContain(
      'Control circuit not designed yet',
    );
    expect(screen.getByTestId('open-item-control_not_designed').textContent).toContain(
      'MDB › Pump',
    );
    expect(screen.getByTestId('open-item-inter_board_wiring_not_modelled').textContent).toContain(
      'Board-to-board wiring not modelled',
    );
  });

  it('shows which requirement produced each circuit and feeder', () => {
    view();
    expect(screen.getByTestId('trace-b1.l1').textContent).toContain('MDB › Pump');
    expect(screen.getByTestId('trace-b1.l1').textContent).toContain('MDB/c1');
    expect(screen.getByTestId('trace-b2.feeder').textContent).toContain('Feeder to DB1 from MDB');
    expect(screen.getByTestId('trace-b2.feeder').textContent).toContain('MDB/c2');
  });

  it('says a refused check in words, a sub-board row by its form number', async () => {
    const preview = vi.fn<typeof previewRequirements>().mockResolvedValue({
      kind: 'refused',
      detail: 'x',
      code: 'sub_board_load_server_owned',
      params: { board: 'MDB', load_index: '2' },
    });
    view({ impls: { preview } });
    fireEvent.click(screen.getByTestId('requirements-check'));
    expect((await screen.findByTestId('requirements-message')).textContent).toBe(
      'MDB, load row 3: a sub-board cannot be entered as a load. Remove the row, then add the sub-board as a board and set its “Fed from” to MDB.',
    );
    expect(preview.mock.calls[0]?.[0].requirementsInput.intake).toBe('guided');
  });

  it('shows a previewed record in place of the saved one', async () => {
    const preview = vi.fn<typeof previewRequirements>().mockResolvedValue({
      kind: 'previewed',
      requirements: record({ questions: [], open_items: [] }),
      design: null,
      findings: [],
    });
    view({ impls: { preview } });
    fireEvent.click(screen.getByTestId('requirements-check'));
    expect(await screen.findByTestId('requirements-preview-note')).toBeTruthy();
    expect(screen.queryByTestId('question-q-fault')).toBeNull();
  });

  it('answers a missing value against the revision, then shows who and when', async () => {
    const answer = vi
      .fn<typeof answerQuestion>()
      .mockResolvedValue({ kind: 'saved', project: savedProject(3) });
    const { onProject } = view({ impls: { answer } });
    expect(screen.getByTestId('question-state-q-fault').textContent).toBe('Open');
    fireEvent.change(screen.getByLabelText('Fault level'), { target: { value: '25' } });
    fireEvent.click(screen.getByTestId('question-answer-q-fault'));
    await waitFor(() => {
      expect(onProject).toHaveBeenCalledWith(savedProject(3), 'answer');
    });
    const sent = answer.mock.calls[0]?.[0];
    expect(sent).toMatchObject({
      id: 'p1',
      revision: 2,
      question: 'q-fault',
      action: 'answer',
      value: '25',
    });
    expect(sent).not.toHaveProperty('by_user_id');
    expect(sent).not.toHaveProperty('at');
    expect(screen.getByTestId('requirements-message').textContent).toBe(
      'Answer recorded as revision 3.',
    );
  });

  it('shows an answered question’s value, who and when, from the server', () => {
    const answered = record();
    const [fault] = answered.questions;
    if (!fault) throw new Error('no question');
    answered.questions[0] = {
      ...fault,
      state: 'answered',
      answer: '25',
      by_name: 'Rana Haddad',
      by_user_id: 'u1',
      at: '2026-11-03T09:00:00Z',
    };
    view({ savedRecord: answered });
    expect(screen.getByTestId('question-state-q-fault').textContent).toBe('Answered');
    expect(screen.getByTestId('question-recorded-q-fault').textContent).toBe(
      'Answered “25” by Rana Haddad on 2026-11-03.',
    );
  });

  it('acknowledges an assumption, saying it is not engineer-validated, with who and when', async () => {
    const after = record();
    const pf = after.questions[1];
    if (!pf) throw new Error('no question');
    after.questions[1] = {
      ...pf,
      state: 'acknowledged',
      by_user_id: 'u1',
      by_name: 'Rana Haddad',
      at: '2026-11-03T10:00:00Z',
    };
    const answer = vi
      .fn<typeof answerQuestion>()
      .mockResolvedValue({ kind: 'recorded', requirements: after });
    view({ impls: { answer } });
    expect(screen.getByTestId('question-q-pf').textContent).toContain('Assumed power factor 0.9');
    fireEvent.click(screen.getByTestId('question-acknowledge-q-pf'));
    await waitFor(() => {
      expect(screen.getByTestId('question-state-q-pf').textContent).toBe(
        'Acknowledged assumption — not engineer-validated',
      );
    });
    expect(answer.mock.calls[0]?.[0]).toMatchObject({ question: 'q-pf', action: 'acknowledge' });
    expect(answer.mock.calls[0]?.[0]).not.toHaveProperty('value');
    expect(screen.getByTestId('question-recorded-q-pf').textContent).toBe(
      'Acknowledged by Rana Haddad on 2026-11-03 — not engineer-validated.',
    );
  });

  it('offers a value for the power factor, and only acknowledgement for a nameplate current', () => {
    view();
    expect(screen.getByTestId('question-answer-q-pf')).toBeTruthy();
    expect(screen.getByTestId('question-acknowledge-q-pf')).toBeTruthy();
    expect(screen.queryByTestId('question-answer-q-flc')).toBeNull();
    expect(screen.getByTestId('question-acknowledge-q-flc')).toBeTruthy();
  });

  it('types an answer into the form before the project is saved, and acknowledges nothing', () => {
    const { onBoardChange } = view({ opened: null });
    fireEvent.change(screen.getByLabelText('Fault level'), { target: { value: '25' } });
    fireEvent.click(screen.getByTestId('question-answer-q-fault'));
    expect(onBoardChange).toHaveBeenCalledWith(0, { faultLevel: '25' });
    expect(screen.getByTestId('question-acknowledge-q-pf')).toHaveProperty('disabled', true);
    expect(screen.getByTestId('question-save-first-q-pf')).toBeTruthy();
  });

  it('holds an answer while there are unsaved changes', () => {
    view({ dirty: true });
    expect(screen.getByTestId('question-answer-q-fault')).toHaveProperty('disabled', true);
    expect(screen.getByTestId('question-dirty-q-fault')).toBeTruthy();
  });

  it('on a derived record, acknowledges nothing and asks for a new revision', () => {
    view({ savedRecord: record({ derived: true }) });
    expect(screen.getByTestId('requirements-derived')).toBeTruthy();
    expect(screen.getByTestId('question-acknowledge-q-pf')).toHaveProperty('disabled', true);
    expect(screen.getByTestId('question-derived-q-pf').textContent).toBe(
      'Save a new revision to record answers.',
    );
  });

  it('says a refused acknowledgement on a derived record', async () => {
    const answer = vi.fn<typeof answerQuestion>().mockResolvedValue({
      kind: 'refused',
      detail: 'derived',
      code: 'requirements_derived',
      params: {},
    });
    view({ impls: { answer } });
    fireEvent.click(screen.getByTestId('question-acknowledge-q-pf'));
    expect((await screen.findByTestId('requirements-message')).textContent).toBe(
      'Save a new revision to record answers.',
    );
  });

  it('repeats a duplicate designation as blocking', () => {
    view({
      findings: [
        {
          code: 'designation_duplicate',
          severity: 'critical',
          board_id: 'MDB',
          message: 'dup',
          requires_engineering_confirmation: true,
          source: 'design',
        } as never,
      ],
    });
    expect(screen.getByTestId('requirements-designation-duplicate').textContent).toContain(
      'MDB: two or more items carry the same printed designation. This blocks approval.',
    );
  });
});

describe('approval by the revision’s author', () => {
  function panel(isAuthor: boolean | undefined, approve = vi.fn<typeof approveRevision>()) {
    const list = vi.fn<typeof listProjects>().mockResolvedValue({
      kind: 'listed',
      page: { permissions: ['approve_revision'], projects: [] },
    });
    renderApp(
      <ProjectsPanel
        token="tok"
        current={null}
        opened={{
          id: 'p1',
          name: 'Tower',
          revision: 2,
          revisions: [1, 2],
          approval: null,
          approvingAs: 'Rana Haddad',
          canApprove: true,
          ...(isAuthor === undefined ? {} : { isAuthor }),
        }}
        onOpened={vi.fn()}
        impls={{ list, approve }}
      />,
      { locale: 'en' },
    );
    return approve;
  }

  it('disables Approve when the server says the signed-in user is the author', () => {
    panel(true);
    confirmScope();
    expect(screen.getByRole('button', { name: 'Approve revision 2' })).toHaveProperty(
      'disabled',
      true,
    );
    expect(screen.getByTestId('approve-self-authored').textContent).toBe(
      'A different team member must approve this revision.',
    );
  });

  it('never guesses from names: offers Approve without the flag', () => {
    // Same display name as the approver, but no flag: two people can share a name.
    panel(undefined);
    confirmScope();
    expect(screen.getByRole('button', { name: 'Approve revision 2' })).toHaveProperty(
      'disabled',
      false,
    );
    expect(screen.queryByTestId('approve-self-authored')).toBeNull();
  });

  it('reads the flag from the opened project', async () => {
    const { isAuthorOf } = await import('@/components/design/projects-panel');
    expect(isAuthorOf({ is_author: true } as never)).toBe(true);
    expect(isAuthorOf({ is_author: false } as never)).toBe(false);
    expect(isAuthorOf({} as never)).toBeUndefined();
  });

  it('says the server’s refusal when it finds the approver is the author', async () => {
    const approve = vi.fn<typeof approveRevision>().mockResolvedValue({
      kind: 'refused',
      detail: 'author',
      code: 'approval_by_author',
      params: {},
    });
    panel(undefined, approve);
    confirmScope();
    fireEvent.click(screen.getByRole('button', { name: 'Approve revision 2' }));
    expect((await screen.findByTestId('projects-message')).textContent).toBe(
      'You saved this revision, so you cannot approve it. Another reviewer must approve it.',
    );
  });
});

describe('the requirements calls', () => {
  it('saves with requirements_input, and previews at the contract’s endpoint', async () => {
    const { saveProject, previewRequirements: preview } = await import('@/lib/design');
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ requirements: record(), design: null }), { status: 200 }),
        ),
      );
    const requirementsInput = { intake: 'guided' as const, control: [] };
    await saveProject({
      token: 't',
      fetchImpl,
      name: 'Tower',
      request: REQUEST,
      requirementsInput,
    });
    const saved = JSON.parse(fetchImpl.mock.calls[0]?.[1]?.body as string) as Record<
      string,
      unknown
    >;
    expect(saved.requirements_input).toEqual(requirementsInput);
    const outcome = await preview({ token: 't', fetchImpl, request: REQUEST, requirementsInput });
    expect(fetchImpl.mock.calls[1]?.[0]).toBe('/api/v1/design/projects/requirements/preview');
    expect(outcome.kind).toBe('previewed');
  });
});

describe('review fixes', () => {
  function acknowledgedBy(by_name: string | undefined) {
    const after = record();
    const pf = after.questions[1];
    if (!pf) throw new Error('no question');
    after.questions[1] = {
      ...pf,
      state: 'acknowledged',
      by_user_id: 'u-raw-id-123',
      at: '2026-11-03T10:00:00Z',
      ...(by_name === undefined ? {} : { by_name }),
    };
    return after;
  }

  it('names a team member, never the raw user id, when the name is empty', () => {
    view({ savedRecord: acknowledgedBy('') });
    const text = screen.getByTestId('question-recorded-q-pf').textContent;
    expect(text).toBe('Acknowledged by a team member on 2026-11-03 — not engineer-validated.');
    expect(text).not.toContain('u-raw-id-123');
  });

  it('names a team member, never the raw user id, when the name is missing', () => {
    view({ savedRecord: acknowledgedBy(undefined) });
    const text = screen.getByTestId('question-recorded-q-pf').textContent;
    expect(text).toBe('Acknowledged by a team member on 2026-11-03 — not engineer-validated.');
  });

  it('shows the power factor default as a default, not a value', () => {
    renderApp(
      <LoadRows
        idPrefix="t"
        loads={[blankLoad(1)]}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
      { locale: 'en' },
    );
    const input = screen.getByLabelText('Power factor');
    expect(input.getAttribute('placeholder')).toBe('default 0.9');
    expect(input).toHaveProperty('value', '');
  });

  function approving(approvingAs: string | null, accountEmail: string | null) {
    const list = vi.fn<typeof listProjects>().mockResolvedValue({
      kind: 'listed',
      page: { permissions: ['approve_revision'], projects: [] },
    });
    renderApp(
      <ProjectsPanel
        token="tok"
        current={null}
        accountEmail={accountEmail}
        opened={{
          id: 'p1',
          name: 'Tower',
          revision: 2,
          revisions: [1, 2],
          approval: null,
          approvingAs,
          canApprove: true,
        }}
        onOpened={vi.fn()}
        impls={{ list }}
      />,
      { locale: 'en' },
    );
    return screen.getByTestId('approving-as').textContent;
  }

  it('names the approving account by its email where it has no display name', () => {
    expect(approving(null, 'rana@example.com')).toBe('You are approving as rana@example.com.');
  });

  it('prefers the display name to the email', () => {
    expect(approving('Rana Haddad', 'rana@example.com')).toBe('You are approving as Rana Haddad.');
  });
});

describe('the account email in a token', () => {
  it('is read for naming only, null for none', async () => {
    const { tokenEmail } = await import('@/components/account-session');
    const part = btoa(JSON.stringify({ sub: 'u1', tid: 't1', email: 'rana@example.com' }));
    expect(tokenEmail(`h.${part}.s`)).toBe('rana@example.com');
    expect(tokenEmail(`h.${btoa(JSON.stringify({ sub: 'u1' }))}.s`)).toBeNull();
    expect(tokenEmail('nonsense')).toBeNull();
  });
});

/** M6d: tick every approval-scope confirmation on the page (the Approve button waits for it). */
function confirmScope() {
  for (const box of screen.queryAllByTestId<HTMLInputElement>('scope-confirm')) {
    if (!box.checked) fireEvent.click(box);
  }
}
