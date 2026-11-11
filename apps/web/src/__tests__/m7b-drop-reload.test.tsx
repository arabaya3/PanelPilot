import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { DRAFT_STORAGE_KEY, parseDraft } from '@/components/design/draft';
import type * as DesignModule from '@/lib/design';
import type {
  listProjects,
  openProject,
  reviseProject,
  SavedProject,
  SavedRequest,
} from '@/lib/design';
import en from '@/messages/en.json';

import { renderApp } from './helpers';

/**
 * The explicit "save without that configuration" choice on a revision whose
 * configuration cannot be carried forward. It is unsaved work: kept in the
 * draft across a reload and offered back, never saved or discarded by itself.
 * Undoing it, switching revision and a successful save each leave the page
 * clean. SYNTHETIC data.
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

const REQUEST = {
  info: { name: 'Legacy tower', number: '', customer: '', consultant: '', contractor: '' },
  boards: [
    {
      name: 'MDB',
      location: null,
      fed_from: null,
      feeder_length_m: null,
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [
        {
          description: 'Lights',
          load: 'lighting',
          power_kw: '1',
          phases: 1,
          power_factor: null,
          controlled: false,
          starter: null,
          length_m: null,
        },
      ],
    },
  ],
  profile: null,
} as unknown as SavedRequest;

function revisionEntry(number: number) {
  return {
    number,
    note: '',
    author: 'e',
    approver_account_recorded: false,
    created_at: '2026-11-10T10:00:00Z',
    validated: true,
    export_status: 'draft',
  };
}

function project(revision: number, status: 'not_recoverable' | 'none'): SavedProject {
  return {
    id: 'p1',
    name: 'Legacy tower',
    revision,
    validated: true,
    export_status: 'draft',
    permissions: ['approve_revision'],
    revisions: Array.from({ length: Math.max(2, revision) }, (_, i) => revisionEntry(i + 1)),
    request: REQUEST,
    findings: [],
    model_v2: null,
    snapshot_schema: 'pp-design/2',
    configuration: null,
    configuration_status: status,
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
  );
}

const config = () => within(screen.getByTestId('view-configuration'));
const drop = () => config().getByTestId<HTMLInputElement>('configuration-drop-unrecoverable');
const unsaved = () => screen.queryAllByText('Unsaved changes').length;

function stored(): Record<string, unknown> | null {
  const raw = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
  return raw === null ? null : (JSON.parse(raw) as Record<string, unknown>);
}

async function openAndTick() {
  fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
  await waitFor(() => {
    expect(drop().checked).toBe(false);
  });
  expect(unsaved()).toBe(0);
  fireEvent.click(drop());
  await waitFor(() => {
    expect(unsaved()).toBeGreaterThan(0);
  });
}

beforeEach(() => {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: ['approve_revision'],
      projects: [{ id: 'p1', name: 'Legacy tower', revisions: 2, updated_at: 'now' }],
    },
  } as never);
  api.open.mockImplementation(({ revision }) =>
    Promise.resolve({ kind: 'saved', project: project(revision ?? 2, 'not_recoverable') }),
  );
  api.revise.mockResolvedValue({ kind: 'saved', project: project(3, 'none') });
});

afterEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
});

describe('"save without that configuration" across a reload', () => {
  it('is kept in the draft, offered back, restored ticked and unsaved, and nothing is saved', async () => {
    const first = renderScreen();
    await openAndTick();
    await waitFor(() => {
      expect(stored()).toMatchObject({
        project: { id: 'p1', revision: 2 },
        configuration: { dropUnrecoverable: true },
      });
    });
    expect(parseDraft(stored())?.configuration?.dropUnrecoverable).toBe(true);
    first.unmount(); // the reload

    renderScreen();
    fireEvent.click(
      within(await screen.findByTestId('draft-offer')).getByText('Restore unsaved work'),
    );
    await waitFor(() => {
      expect(drop().checked).toBe(true);
    });
    expect(unsaved()).toBeGreaterThan(0);
    // Restoring is not a save, and opens only the drafted revision.
    expect(api.revise).not.toHaveBeenCalled();
    expect(api.open).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'p1', revision: 2 }));
  });

  it('declining the offer keeps nothing ticked and saves nothing', async () => {
    const first = renderScreen();
    await openAndTick();
    await waitFor(() => {
      expect(stored()).not.toBeNull();
    });
    first.unmount();
    renderScreen();
    await screen.findByTestId('draft-offer');
    expect(api.revise).not.toHaveBeenCalled();
    expect(screen.queryByTestId('configuration-drop-unrecoverable')).toBeNull();
  });

  it('unticking (undo) makes the page clean and clears the draft', async () => {
    renderScreen();
    await openAndTick();
    await waitFor(() => {
      expect(stored()).not.toBeNull();
    });
    fireEvent.click(drop());
    await waitFor(() => {
      expect(unsaved()).toBe(0);
    });
    expect(stored()).toBeNull();
    expect(api.revise).not.toHaveBeenCalled();
  });

  it('switching revision asks first; keeping stays ticked, discarding opens it unticked and clean', async () => {
    renderScreen();
    await openAndTick();
    const select = () => {
      const found = screen
        .getAllByLabelText('Revision')
        .find((element): element is HTMLSelectElement => element instanceof HTMLSelectElement);
      if (found === undefined) throw new Error('no revision select');
      return found;
    };
    const dialog = () => within(screen.getByTestId('unsaved-dialog'));
    const asking = () => screen.getByTestId('unsaved-dialog').closest('dialog')?.open ?? false;
    const opens = api.open.mock.calls.length;

    fireEvent.change(select(), { target: { value: '1' } });
    await waitFor(() => {
      expect(asking()).toBe(true);
    });
    fireEvent.click(dialog().getByText('Keep editing'));
    expect(api.open.mock.calls.length).toBe(opens);
    expect(drop().checked).toBe(true);

    fireEvent.change(select(), { target: { value: '1' } });
    await waitFor(() => {
      expect(asking()).toBe(true);
    });
    fireEvent.click(dialog().getByText('Discard changes'));
    await waitFor(() => {
      expect(api.open).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 1 }));
    });
    await waitFor(() => {
      expect(drop().checked).toBe(false);
    });
    expect(unsaved()).toBe(0);
    expect(api.revise).not.toHaveBeenCalled();
  });

  it('a restored choice saves the removal only when Save is pressed, then reads clean', async () => {
    const first = renderScreen();
    await openAndTick();
    await waitFor(() => {
      expect(stored()).not.toBeNull();
    });
    first.unmount();
    renderScreen();
    fireEvent.click(
      within(await screen.findByTestId('draft-offer')).getByText('Restore unsaved work'),
    );
    await waitFor(() => {
      expect(drop().checked).toBe(true);
    });
    expect(api.revise).not.toHaveBeenCalled();
    fireEvent.click(
      within(screen.getByTestId('view-overview')).getByRole('button', {
        name: en.design.projects.saveRevision,
      }),
    );
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
    expect(api.revise.mock.calls[0]?.[0]).toMatchObject({ configurationRemoved: true });
    await waitFor(() => {
      expect(unsaved()).toBe(0);
    });
    expect(stored()).toBeNull();
  });
});
