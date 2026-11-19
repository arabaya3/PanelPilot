import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { parseDraft, storedDrafts } from '@/components/design/draft';
import type * as DesignModule from '@/lib/design';
import type {
  listProjects,
  openProject,
  reviseProject,
  SavedProject,
  SavedRequest,
} from '@/lib/design';
import { deriveId } from '@/lib/sha256';
import en from '@/messages/en.json';

import { LINK_MODEL } from './feeder-link-fixtures';
import { renderApp } from './helpers';

/**
 * M8: define a component and its terminals without JSON, pick one of its
 * terminals as a feeder-link end before saving, and save once; references are
 * checked before anything is removed; a refusal of the definition is shown at
 * it; a reload keeps the unsaved definition. SYNTHETIC data; the API is mocked.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  open: vi.fn<typeof openProject>(),
  revise: vi.fn<typeof reviseProject>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return { ...actual, listProjects: api.list, openProject: api.open, reviseProject: api.revise };
});

function load(description: string) {
  return {
    description,
    load: 'lighting',
    power_kw: '1',
    phases: 1,
    power_factor: null,
    controlled: false,
    starter: null,
    length_m: null,
  };
}

const REQUEST = {
  info: { name: 'Defined tower', number: '', customer: '', consultant: '', contractor: '' },
  boards: [
    {
      name: 'MB-1',
      location: null,
      fed_from: null,
      feeder_length_m: null,
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [load('Lights')],
    },
    {
      name: 'SB-1',
      location: null,
      fed_from: 'MB-1',
      feeder_length_m: '25',
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [load('Sockets')],
    },
  ],
  profile: null,
} as unknown as SavedRequest;

function project(): SavedProject {
  return {
    id: 'p1',
    name: 'Defined tower',
    revision: 1,
    validated: true,
    export_status: 'draft',
    permissions: ['approve_revision'],
    revisions: [
      {
        number: 1,
        note: '',
        author: 'e',
        approver_account_recorded: false,
        created_at: '2026-11-15T10:00:00Z',
        validated: true,
        export_status: 'draft',
      },
    ],
    request: REQUEST,
    findings: [],
    model_v2: { ...LINK_MODEL, terminals: [] },
    snapshot_schema: 'pp-design/3',
    written_schema: 'pp-design/3',
    configuration: null,
    configuration_status: 'none',
  } as unknown as SavedProject;
}

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

function renderScreen() {
  return renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      saveImpl={vi.fn()}
    />,
    { locale: 'en' },
  );
}

const config = () => within(screen.getByTestId('view-configuration'));
const d = en.configuration.definitions;

async function openStored() {
  fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
  await waitFor(() => {
    expect(screen.getByLabelText<HTMLInputElement>('Project name').value).toBe('Defined tower');
  });
}

/** Everything through the guided editor: no JSON is typed or imported. */
function defineAndPlace() {
  const add = within(config().getByTestId('add-definition'));
  fireEvent.change(add.getByTestId('add-definition-id'), { target: { value: 'synth-isolator' } });
  fireEvent.click(add.getByTestId('add-definition-submit'));
  const row = within(config().getByTestId('definition-entry'));
  for (const marking of ['L1', 'L2']) {
    fireEvent.change(row.getByTestId('definition-new-terminal'), { target: { value: marking } });
    fireEvent.click(row.getByTestId('definition-add-terminal'));
  }
  const component = within(config().getByTestId('add-component'));
  fireEvent.change(component.getByTestId('add-component-board'), { target: { value: 'MB-1' } });
  const choice = component
    .getAllByTestId<HTMLInputElement>('template-choice')
    .find((input) => input.value === 'tenant/synth-isolator/v1');
  if (choice === undefined) throw new Error('the new definition is not offered');
  fireEvent.click(choice);
  fireEvent.change(component.getByTestId('add-component-designation'), {
    target: { value: 'Q9' },
  });
  fireEvent.click(component.getByTestId('add-component-submit'));
}

function showBoard(name: string) {
  fireEvent.click(
    within(screen.getByTestId('board-tabs')).getByRole('button', { name, hidden: true }),
  );
}

function storedDraft(): Record<string, unknown> | null {
  // M14: drafts are kept per project; this is the most recently written one.
  return (storedDrafts()[0] as Record<string, unknown> | undefined) ?? null;
}

beforeEach(() => {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: ['approve_revision'],
      projects: [{ id: 'p1', name: 'Defined tower', revisions: 1, updated_at: 'now' }],
    },
  } as never);
  api.open.mockResolvedValue({ kind: 'saved', project: project() });
});

afterEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
});

describe('a component defined in the guided editor', () => {
  it('is flagged unreviewed and infers nothing', async () => {
    renderScreen();
    await openStored();
    defineAndPlace();
    const row = within(config().getByTestId('definition-entry'));
    expect(row.getByTestId('definition-unreviewed').textContent).toBe(d.unreviewed);
    expect(row.getByTestId<HTMLSelectElement>('definition-function').value).toBe('unspecified');
    for (const kind of row.getAllByTestId<HTMLSelectElement>('definition-terminal-kind')) {
      expect(kind.value).toBe('unspecified');
    }
    for (const required of row.getAllByTestId<HTMLSelectElement>('definition-terminal-required')) {
      expect(required.value).toBe('unstated');
    }
    expect(within(config().getByTestId('component-entry')).getByText('Terminals: L1, L2')).toBe(
      config().getByTestId('component-terminals'),
    );
  });

  it('offers its terminal as a feeder-link end before saving, and saves both in one Save', async () => {
    api.revise.mockResolvedValue({ kind: 'saved', project: project() });
    renderScreen();
    await openStored();
    defineAndPlace();
    showBoard('SB-1');
    const link = within(screen.getByTestId('feeder-link-form'));
    const select = link.getByTestId<HTMLSelectElement>('link-from-terminal');
    const option = Array.from(select.options).find((item) => item.text === 'Q9:L1 — MB-1');
    if (option === undefined) throw new Error('the defined terminal is not offered');
    fireEvent.change(select, { target: { value: option.value } });
    expect(link.getByTestId('link-from-terminal-chosen').textContent).toContain('Q9:L1');
    // Only MB-1's terminals are offered at the MB-1 end; none at SB-1's.
    expect(
      Array.from(link.getByTestId<HTMLSelectElement>('link-to-terminal').options).map(
        (item) => item.text,
      ),
    ).toEqual([en.feederLink.terminalNone]);

    fireEvent.click(screen.getByText('Save as new revision', { selector: 'button' }));
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
    const call = api.revise.mock.lastCall?.[0];
    const sent = call?.configuration as Record<string, Record<string, unknown>[]>;
    const component = sent.components?.[0] as { id: string; template: string };
    expect(component.template).toBe('tenant/synth-isolator/v1');
    const terminals = sent.templates?.[0]?.terminals as Array<{ id: string; marking: string }>;
    const l1 = terminals.find((terminal) => terminal.marking === 'L1');
    const request = call?.request as unknown as { feeder_links: unknown };
    expect(request.feeder_links).toEqual([
      {
        fed_board: 'SB-1',
        from_end: {
          terminal: { component: deriveId('k', component.id), terminal: l1?.id },
        },
      },
    ]);
  });

  it('will not remove the definition, the component or a linked terminal while they are used', async () => {
    renderScreen();
    await openStored();
    defineAndPlace();
    const row = () => within(config().getByTestId('definition-entry'));
    expect(row().getByTestId<HTMLButtonElement>('definition-remove').disabled).toBe(true);
    expect(row().getByTestId('definition-users').textContent).toContain('component Q9');
    showBoard('SB-1');
    const select = within(screen.getByTestId('feeder-link-form')).getByTestId<HTMLSelectElement>(
      'link-from-terminal',
    );
    const option = Array.from(select.options).find((item) => item.text === 'Q9:L1 — MB-1');
    fireEvent.change(select, { target: { value: option?.value } });
    const [l1, l2] = row().getAllByTestId<HTMLButtonElement>('definition-terminal-remove');
    expect(l1?.disabled).toBe(true);
    expect(l2?.disabled).toBe(false);
    expect(row().getByTestId('definition-terminal-linked').textContent).toContain(
      'the supply end of the feeder link to SB-1',
    );
    const component = within(config().getByTestId('component-entry'));
    expect(component.getByTestId<HTMLButtonElement>('component-remove').disabled).toBe(true);
    expect(component.getByTestId('component-linked').textContent).toContain('SB-1');
  });

  it('says a blank or repeated marking beside the terminal', async () => {
    renderScreen();
    await openStored();
    defineAndPlace();
    const row = within(config().getByTestId('definition-entry'));
    const [, second] = row.getAllByTestId('definition-terminal-marking');
    fireEvent.change(second as HTMLElement, { target: { value: 'L1' } });
    expect(row.getAllByTestId('definition-terminal-problem').map((p) => p.textContent)).toEqual([
      d.markingProblem.duplicate,
      d.markingProblem.duplicate,
    ]);
    fireEvent.change(row.getByTestId('definition-new-terminal'), { target: { value: 'L1' } });
    expect(row.getByTestId('definition-new-terminal-problem').textContent).toBe(
      d.markingProblem.duplicate,
    );
    expect(row.getByTestId<HTMLButtonElement>('definition-add-terminal').disabled).toBe(true);
    const add = within(config().getByTestId('add-definition'));
    fireEvent.change(add.getByTestId('add-definition-id'), { target: { value: 'Bad Id' } });
    expect(add.getByTestId('add-definition-problem').textContent).toBe(d.idProblem.invalid);
  });

  it('shows a refusal of the definition at it, and keeps every input', async () => {
    api.revise.mockResolvedValue({
      kind: 'refused',
      detail: 'template invalid',
      code: 'configuration_refused',
      params: {
        refusal: 'template_invalid',
        kind: 'template',
        ref: 'tenant/synth-isolator/v1',
      },
    });
    renderScreen();
    await openStored();
    defineAndPlace();
    fireEvent.click(screen.getByText('Save as new revision', { selector: 'button' }));
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(config().getByTestId('definition-entry').getAttribute('data-conflict')).toBe('true');
    });
    fireEvent.click(config().getByTestId('conflict-edit'));
    expect(document.activeElement).toBe(config().getByTestId('definition-entry'));
    expect(config().getAllByTestId('definition-terminal')).toHaveLength(2);
    expect(config().getAllByTestId('component-entry')).toHaveLength(1);
  });

  it('survives a reload as unsaved work, and is restored without saving', async () => {
    const first = renderScreen();
    await openStored();
    defineAndPlace();
    await waitFor(() => {
      expect(storedDraft()).toMatchObject({ project: { id: 'p1', revision: 1 } });
    });
    expect(JSON.stringify(parseDraft(storedDraft())?.configuration?.doc)).toContain(
      'tenant/synth-isolator/v1',
    );
    first.unmount();
    renderScreen();
    fireEvent.click(
      within(await screen.findByTestId('draft-offer')).getByText('Restore unsaved work'),
    );
    await waitFor(() => {
      expect(config().getAllByTestId('definition-terminal')).toHaveLength(2);
    });
    expect(screen.getAllByText('Unsaved changes').length).toBeGreaterThan(0);
    expect(api.revise).not.toHaveBeenCalled();
  });
});
