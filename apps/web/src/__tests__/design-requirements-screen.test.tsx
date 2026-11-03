import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen, snapshotOf } from '@/components/design-screen';
import { blankBoard } from '@/components/design/schedule';
import type {
  listProjects,
  openProject,
  RequirementsRecord,
  reviseProject,
  saveProject,
  SavedProject,
  SavedRequest,
} from '@/lib/design';
import type * as DesignModule from '@/lib/design';

import { renderApp } from './helpers';

/**
 * Control needs on the design screen: they are work like any field. They
 * survive switching views, count as unsaved changes (and so are guarded),
 * are sent with a save, and come back from the saved record on reopening.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  save: vi.fn<typeof saveProject>(),
  revise: vi.fn<typeof reviseProject>(),
  open: vi.fn<typeof openProject>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return {
    ...actual,
    listProjects: api.list,
    saveProject: api.save,
    reviseProject: api.revise,
    openProject: api.open,
  };
});

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const REQUEST = {
  info: { name: 'Stored tower', number: '', customer: '', consultant: '', contractor: '' },
  boards: [
    {
      name: 'MDB',
      location: null,
      fed_from: null,
      feeder_length_m: null,
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [
        {
          description: 'Pump',
          load: 'motor',
          power_kw: '7.5',
          phases: 3,
          power_factor: null,
          controlled: false,
          starter: 'dol',
          length_m: null,
        },
      ],
    },
  ],
  profile: null,
} as unknown as SavedRequest;

const RECORD: RequirementsRecord = {
  schema: 'pp-req/1',
  intake: 'guided',
  boards: [{ ref: 'b1', name: 'MDB', fed_from_ref: null }],
  loads: [{ ref: 'b1.l1', board_ref: 'b1', description: 'Pump' }],
  control: [
    {
      ref: 'b1.c1',
      board_ref: 'b1',
      load_ref: 'b1.l1',
      needs: ['start_stop', 'running_indicator'],
      status: 'not_designed',
    },
  ],
  questions: [],
  open_items: [],
  trace: [],
};

function stored(): SavedProject {
  return {
    id: 'p1',
    name: 'Stored tower',
    revision: 1,
    validated: true,
    findings: [],
    export_status: 'not_validated',
    permissions: ['approve_revision'],
    revisions: [
      {
        number: 1,
        note: '',
        author: 'e',
        approver_account_recorded: false,
        created_at: '2026-11-03T10:00:00Z',
        validated: true,
        export_status: 'not_validated' as const,
      },
    ],
    request: REQUEST,
    ...({ requirements: RECORD } as object),
  };
}

beforeEach(() => {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: ['approve_revision'],
      projects: [{ id: 'p1', name: 'Stored tower', revisions: 1, updated_at: 'now' }],
    },
  });
  api.open.mockResolvedValue({ kind: 'saved', project: stored() });
  api.save.mockResolvedValue({ kind: 'saved', project: stored() });
});

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/');
});

function viewTab(label: string): HTMLElement {
  const tab = screen
    .getByText(label, { selector: '[role="tab"], [role="tab"] *' })
    .closest('[role="tab"]');
  if (!(tab instanceof HTMLElement)) throw new Error(`no tab ${label}`);
  return tab;
}

function renderScreen() {
  renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
    />,
  );
}

const requirements = () => within(screen.getByTestId('view-requirements'));
const needBox = (need: string) =>
  requirements().getAllByTestId(new RegExp(`^control-${need}-`))[0] as HTMLInputElement;

async function openStored() {
  const row = await screen.findByTestId('project-p1');
  fireEvent.click(within(row).getByText('Open'));
  await waitFor(() => {
    expect(api.open).toHaveBeenCalled();
  });
  await waitFor(() => {
    expect(
      within(screen.getByTestId('view-overview')).getByLabelText<HTMLInputElement>('Project name')
        .value,
    ).toBe('Stored tower');
  });
}

describe('control needs on the design screen', () => {
  it('count in the unsaved-changes snapshot, row by row, keys left out', () => {
    const board = blankBoard(0, 1, 'MDB');
    const info = { name: 'T', number: '', customer: '', consultant: '', contractor: '' };
    const none = snapshotOf(info, [board], '', null, 'X');
    expect(snapshotOf(info, [board], '', null, 'X', {})).toBe(none);
    expect(snapshotOf(info, [board], '', null, 'X', { 1: [] })).toBe(none);
    const ticked = snapshotOf(info, [board], '', null, 'X', { 1: ['start_stop'] });
    expect(ticked).not.toBe(none);
    // Renumbered keys (an opening) compare as the same work.
    const renumbered = {
      ...board,
      key: 7,
      loads: board.loads.map((load) => ({ ...load, key: 9 })),
    };
    expect(snapshotOf(info, [renumbered], '', null, 'X', { 9: ['start_stop'] })).toBe(ticked);
  });

  it('mark the page unsaved, and survive switching views', async () => {
    renderScreen();
    await screen.findByTestId('projects-panel');
    fireEvent.click(viewTab('Requirements'));
    expect(screen.queryByText('Unsaved changes')).toBeNull();
    fireEvent.click(needBox('start_stop'));
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
    fireEvent.click(viewTab('Load schedule'));
    fireEvent.click(viewTab('Requirements'));
    expect(needBox('start_stop').checked).toBe(true);
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
    // Unticked again: the work is back as it was.
    fireEvent.click(needBox('start_stop'));
    expect(screen.queryByText('Unsaved changes')).toBeNull();
  });

  it('are restored from the saved record on reopening, as clean', async () => {
    renderScreen();
    await openStored();
    fireEvent.click(viewTab('Requirements'));
    expect(needBox('start_stop').checked).toBe(true);
    expect(needBox('running_indicator').checked).toBe(true);
    expect(screen.queryByText('Unsaved changes')).toBeNull();
    fireEvent.click(needBox('running_indicator'));
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
  });

  it('are asked about before opening a project over them', async () => {
    renderScreen();
    await screen.findByTestId('projects-panel');
    fireEvent.click(viewTab('Requirements'));
    fireEvent.click(needBox('start_stop'));
    fireEvent.click(viewTab('Overview'));
    const row = await screen.findByTestId('project-p1');
    fireEvent.click(within(row).getByText('Open'));
    await waitFor(() => {
      expect(screen.getByTestId('unsaved-dialog').closest('dialog')?.open).toBe(true);
    });
    expect(api.open).not.toHaveBeenCalled();
  });
});
