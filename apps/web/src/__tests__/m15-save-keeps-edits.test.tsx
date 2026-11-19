import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { draftBoards, readDraft, storedDrafts, writeDraft } from '@/components/design/draft';
import { blankBoard } from '@/components/design/schedule';
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
 * A reload with unsaved configuration edits. The page was dirty (the edits
 * count as unsaved) and wrote a draft, but the draft did not hold the
 * configuration: restoring reopened the revision with its STORED
 * configuration and the edits were gone without a word. SYNTHETIC data.
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

const STORED = {
  templates: [],
  patterns: [],
  strips: [
    {
      id: 'strip-a',
      board: 'MDB',
      designation: { function: null, location: null, product: 'X1' },
      template: null,
      points: [{ id: 'p-a', marking: '1' }],
    },
  ],
  components: [],
  bindings: [],
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

function project(): SavedProject {
  return {
    id: 'p1',
    name: 'Stored tower',
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
        created_at: '2026-11-10T10:00:00Z',
        validated: true,
        export_status: 'draft',
      },
    ],
    request: REQUEST,
    findings: [],
    model_v2: null,
    snapshot_schema: 'pp-design/2',
    configuration: STORED,
    configuration_status: 'stored',
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
const designation = () => config().getByTestId<HTMLInputElement>('strip-designation');

beforeEach(() => {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: ['approve_revision'],
      projects: [{ id: 'p1', name: 'Stored tower', revisions: 1, updated_at: 'now' }],
    },
  } as never);
  api.open.mockResolvedValue({ kind: 'saved', project: project() });
  api.revise.mockResolvedValue({ kind: 'saved', project: project() });
});

afterEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  // A view chosen in one test (a tab click sets ?view=) must not open the next.
  window.history.replaceState(null, '', '/');
});

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

const overview = () => within(screen.getByTestId('view-overview'));
const nameField = () => overview().getByLabelText<HTMLInputElement>('Project name');
const unsaved = () =>
  screen.getByTestId('project-current').textContent.includes('with unsaved changes');
const saveButton = () => overview().getByRole('button', { name: en.design.projects.saveRevision });

/** Revision 2 as the server stores it: exactly what was submitted. */
function savedAs(name: string, designationValue: string): SavedProject {
  const base = project();
  return {
    ...base,
    revision: 2,
    revisions: [...base.revisions, { ...base.revisions[0], number: 2 }],
    request: { ...REQUEST, info: { ...REQUEST.info, name } },
    configuration: {
      ...STORED,
      strips: [
        {
          ...STORED.strips[0],
          designation: { function: null, location: null, product: designationValue },
        },
      ],
    },
  } as unknown as SavedProject;
}

async function openEditAndSave(pending: Promise<Awaited<ReturnType<typeof reviseProject>>>) {
  fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
  await waitFor(() => {
    expect(designation().value).toBe('X1');
  });
  fireEvent.change(designation(), { target: { value: 'X7' } });
  fireEvent.change(nameField(), { target: { value: 'Submitted name' } });
  api.revise.mockReturnValueOnce(pending);
  fireEvent.click(saveButton());
  await waitFor(() => {
    expect(api.revise).toHaveBeenCalledTimes(1);
  });
  // Edits made while the save is running.
  fireEvent.change(designation(), { target: { value: 'X9' } });
  fireEvent.change(nameField(), { target: { value: 'Newer name' } });
}

describe('edits made while Save is running', () => {
  it('are kept, visible and unsaved when the save succeeds; the server got the submitted version', async () => {
    const pending = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    renderScreen();
    await openEditAndSave(pending.promise);
    await act(async () => {
      pending.resolve({ kind: 'saved', project: savedAs('Submitted name', 'X7') });
      await pending.promise;
    });
    const sent = api.revise.mock.calls[0]?.[0];
    expect(JSON.stringify(sent?.configuration)).toContain('X7');
    expect(sent?.request.info.name).toBe('Submitted name');
    // The newer work is still on the page, and unsaved.
    expect(designation().value).toBe('X9');
    expect(nameField().value).toBe('Newer name');
    expect(unsaved()).toBe(true);
    // It is kept for recovery after a reload.
    await waitFor(() => {
      expect(JSON.stringify(storedDrafts()[0]?.configuration?.doc)).toContain('X9');
    });
    // Undoing back to exactly what was saved reads clean.
    fireEvent.change(designation(), { target: { value: 'X7' } });
    fireEvent.change(nameField(), { target: { value: 'Submitted name' } });
    await waitFor(() => {
      expect(unsaved()).toBe(false);
    });
  });

  it('are kept when the save fails', async () => {
    const pending = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    renderScreen();
    await openEditAndSave(pending.promise);
    await act(async () => {
      pending.resolve({ kind: 'failed' });
      await pending.promise;
    });
    expect(designation().value).toBe('X9');
    expect(nameField().value).toBe('Newer name');
    expect(unsaved()).toBe(true);
  });

  it('with nothing edited during the save, the page reads clean on the stored revision', async () => {
    const pending = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    fireEvent.change(designation(), { target: { value: 'X7' } });
    api.revise.mockReturnValueOnce(pending.promise);
    fireEvent.click(saveButton());
    await act(async () => {
      pending.resolve({ kind: 'saved', project: savedAs('Stored tower', 'X7') });
      await pending.promise;
    });
    expect(designation().value).toBe('X7');
    await waitFor(() => {
      expect(unsaved()).toBe(false);
    });
  });

  it('consecutive saves each store what was submitted, keeping what came after', async () => {
    const first = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    renderScreen();
    await openEditAndSave(first.promise);
    await act(async () => {
      first.resolve({ kind: 'saved', project: savedAs('Submitted name', 'X7') });
      await first.promise;
    });
    expect(designation().value).toBe('X9');
    api.revise.mockResolvedValueOnce({ kind: 'saved', project: savedAs('Newer name', 'X9') });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(2);
    });
    expect(JSON.stringify(api.revise.mock.calls[1]?.[0]?.configuration)).toContain('X9');
    await waitFor(() => {
      expect(unsaved()).toBe(false);
    });
    expect(designation().value).toBe('X9');
  });

  it('during Save before a switch, edits made while saving are kept and asked about again', async () => {
    const pending = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    api.list.mockResolvedValue({
      kind: 'listed',
      page: {
        permissions: [],
        projects: [
          { id: 'p1', name: 'Stored tower', revisions: 1, updated_at: '2026-11-01T10:00:00Z' },
          { id: 'p2', name: 'Second plant', revisions: 1, updated_at: '2026-11-02T10:00:00Z' },
        ],
      },
    });
    renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    fireEvent.change(designation(), { target: { value: 'X7' } });
    api.revise.mockReturnValueOnce(pending.promise);
    fireEvent.click(within(screen.getByTestId('project-p2')).getByText('Open'));
    const dialog = () =>
      within(screen.getByTestId('switch-dialog').closest('dialog') as HTMLElement);
    await waitFor(() => {
      expect(screen.getByTestId('switch-dialog').closest('dialog')?.open).toBe(true);
    });
    fireEvent.click(dialog().getByTestId('switch-save'));
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
    fireEvent.change(designation(), { target: { value: 'X9' } });
    await act(async () => {
      pending.resolve({ kind: 'saved', project: savedAs('Stored tower', 'X7') });
      await pending.promise;
    });
    // Asked again: the newer edit is neither replaced nor switched away from.
    await waitFor(() => {
      expect(screen.getByTestId('switch-dialog').closest('dialog')?.open).toBe(true);
    });
    expect(designation().value).toBe('X9');
    fireEvent.click(dialog().getByTestId('switch-cancel'));
    expect(designation().value).toBe('X9');
    expect(JSON.stringify(api.revise.mock.calls[0]?.[0]?.configuration)).toContain('X7');
  });

  it('the switch dialog is rendered at the page level, never inside a hidden view', async () => {
    api.list.mockResolvedValue({
      kind: 'listed',
      page: {
        permissions: [],
        projects: [
          { id: 'p1', name: 'Stored tower', revisions: 1, updated_at: '2026-11-01T10:00:00Z' },
          { id: 'p2', name: 'Second plant', revisions: 1, updated_at: '2026-11-02T10:00:00Z' },
        ],
      },
    });
    renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    fireEvent.change(designation(), { target: { value: 'X7' } });
    fireEvent.click(within(screen.getByTestId('project-p2')).getByText('Open'));
    const dialog = await waitFor(() => {
      const found = screen.getByTestId('switch-dialog').closest('dialog');
      expect(found?.open).toBe(true);
      return found as HTMLDialogElement;
    });
    expect(dialog.closest('[data-testid="view-overview"]')).toBeNull();
    expect(dialog.closest('[data-testid="projects-panel"]')).toBeNull();
  });

  it('from another tab, Discard in the dialog asked again leaves focus on the selected tab', async () => {
    const pending = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    api.list.mockResolvedValue({
      kind: 'listed',
      page: {
        permissions: [],
        projects: [
          { id: 'p1', name: 'Stored tower', revisions: 1, updated_at: '2026-11-01T10:00:00Z' },
          { id: 'p2', name: 'Second plant', revisions: 1, updated_at: '2026-11-02T10:00:00Z' },
        ],
      },
    });
    api.open.mockResolvedValueOnce({ kind: 'saved', project: project() });
    api.open.mockResolvedValueOnce({
      kind: 'saved',
      project: { ...project(), id: 'p2', name: 'Second plant' },
    });
    renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    fireEvent.change(designation(), { target: { value: 'X7' } });
    api.revise.mockReturnValueOnce(pending.promise);
    fireEvent.click(within(screen.getByTestId('project-p2')).getByText('Open'));
    const dialog = () =>
      within(screen.getByTestId('switch-dialog').closest('dialog') as HTMLElement);
    await waitFor(() => {
      expect(screen.getByTestId('switch-dialog').closest('dialog')?.open).toBe(true);
    });
    fireEvent.click(dialog().getByTestId('switch-save'));
    // On the Configuration tab now, editing while the save runs.
    const tab = screen.getByRole('tab', { name: 'Configuration' });
    fireEvent.click(tab);
    designation().focus();
    fireEvent.change(designation(), { target: { value: 'X9' } });
    await act(async () => {
      pending.resolve({ kind: 'saved', project: savedAs('Stored tower', 'X7') });
      await pending.promise;
    });
    await waitFor(() => {
      expect(screen.getByTestId('switch-dialog').closest('dialog')?.open).toBe(true);
    });
    fireEvent.click(dialog().getByTestId('switch-discard'));
    await waitFor(() => {
      expect(screen.getByTestId('project-opened-name').textContent).toContain('Second plant');
    });
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Configuration' }));
    });
  });

  it('an ordinary Open from Overview still puts focus on the opened project, even if focus fell to the page', async () => {
    renderScreen();
    const open = within(await screen.findByTestId('project-p1')).getByText('Open');
    // As in a browser, the pressed button is disabled while opening and focus falls to the page.
    open.focus();
    fireEvent.click(open);
    (document.activeElement as HTMLElement | null)?.blur();
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByTestId('project-opened-name'));
    });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(document.activeElement).toBe(screen.getByTestId('project-opened-name'));
  });

  for (const outcome of ['saved', 'failed'] as const) {
    it(`a draft restored while Save is in flight is not overwritten by its ${outcome} answer`, async () => {
      // A copy of p1 kept earlier in this tab (another reload).
      writeDraft({
        session: 's',
        project: { id: 'p1', revision: 1 },
        clean: '',
        info: { name: 'Restored name', number: '', customer: '', consultant: '', contractor: '' },
        boards: draftBoards([blankBoard(0, 1, 'MDB')]),
        profileText: '',
        language: null,
        market: '',
        configuration: {
          doc: {
            ...STORED,
            strips: [
              {
                ...STORED.strips[0],
                designation: { function: null, location: null, product: 'XD' },
              },
            ],
          },
          dropUnrecoverable: false,
        },
      });
      const pending = deferred<Awaited<ReturnType<typeof reviseProject>>>();
      renderScreen();
      fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
      await waitFor(() => {
        expect(designation().value).toBe('X1');
      });
      const offer = await screen.findByTestId('draft-offer');
      fireEvent.change(designation(), { target: { value: 'X7' } });
      api.revise.mockReturnValueOnce(pending.promise);
      fireEvent.click(saveButton());
      await waitFor(() => {
        expect(api.revise).toHaveBeenCalledTimes(1);
      });
      // Restore the kept copy while the save runs (the page asks first: discard).
      fireEvent.click(within(offer).getByText('Restore unsaved work'));
      fireEvent.click(within(screen.getByTestId('unsaved-dialog')).getByText('Discard changes'));
      await waitFor(() => {
        expect(designation().value).toBe('XD');
      });
      expect(nameField().value).toBe('Restored name');
      await act(async () => {
        pending.resolve(
          outcome === 'saved'
            ? { kind: 'saved', project: savedAs('Stored tower', 'X7') }
            : { kind: 'failed' },
        );
        await pending.promise;
      });
      // The restored workspace is untouched by the late answer.
      expect(designation().value).toBe('XD');
      expect(nameField().value).toBe('Restored name');
      expect(unsaved()).toBe(true);
      // Its recovery copy is still kept, with the restored work.
      await waitFor(() => {
        expect(JSON.stringify(readDraft('s', 'p1')?.configuration?.doc)).toContain('XD');
      });
    });
  }

  it('a restore whose project cannot be reopened, during a save, is not overwritten by the save answer', async () => {
    writeDraft({
      session: 's',
      project: { id: 'p1', revision: 1 },
      clean: '',
      info: { name: 'Restored name', number: '', customer: '', consultant: '', contractor: '' },
      boards: draftBoards([blankBoard(0, 1, 'MDB')]),
      profileText: '',
      language: null,
      market: '',
      configuration: {
        doc: {
          ...STORED,
          strips: [
            { ...STORED.strips[0], designation: { function: null, location: null, product: 'XD' } },
          ],
        },
        dropUnrecoverable: false,
      },
    });
    const pending = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    const offer = await screen.findByTestId('draft-offer');
    fireEvent.change(designation(), { target: { value: 'X7' } });
    api.revise.mockReturnValueOnce(pending.promise);
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
    // The restore's reopen fails (the project is gone, say).
    api.open.mockResolvedValueOnce({ kind: 'failed' });
    fireEvent.click(within(offer).getByText('Restore unsaved work'));
    fireEvent.click(within(screen.getByTestId('unsaved-dialog')).getByText('Discard changes'));
    await screen.findByTestId('draft-project-gone');
    await waitFor(() => {
      expect(nameField().value).toBe('Restored name');
    });
    await act(async () => {
      pending.resolve({ kind: 'saved', project: savedAs('Stored tower', 'X7') });
      await pending.promise;
    });
    // The late answer neither reopens the project nor marks the restored work saved.
    expect(screen.getByTestId('draft-project-gone')).toBeTruthy();
    expect(nameField().value).toBe('Restored name');
    // Its unsaved configuration came back too (the project is still the one open).
    expect(designation().value).toBe('XD');
    expect(screen.getAllByText('Unsaved changes').length).toBeGreaterThan(0);
  });

  it('restoring a copy over discarded configuration edits keeps that copy, and later edits are kept too', async () => {
    // XD typed and left by a reload: the app writes the copy (same schedule).
    const first = renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    fireEvent.change(designation(), { target: { value: 'XD' } });
    await waitFor(() => {
      expect(JSON.stringify(storedDrafts())).toContain('XD');
    });
    first.unmount();
    renderScreen();
    await screen.findByTestId('draft-offer');
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    const offer = await screen.findByTestId('draft-offer');
    fireEvent.change(designation(), { target: { value: 'X7' } });
    await waitFor(() => {
      expect(storedDrafts()).toHaveLength(2);
    });
    fireEvent.click(within(offer).getByText('Restore unsaved work'));
    fireEvent.click(within(screen.getByTestId('unsaved-dialog')).getByText('Discard changes'));
    await waitFor(() => {
      expect(designation().value).toBe('XD');
    });
    // The restored copy is kept (only the discarded one went).
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(JSON.stringify(storedDrafts())).toContain('XD');
    expect(JSON.stringify(storedDrafts())).not.toContain('X7');
    // Later configuration edits are kept as well.
    fireEvent.change(designation(), { target: { value: 'XE' } });
    await waitFor(() => {
      expect(JSON.stringify(storedDrafts())).toContain('XE');
    });
  });

  it('a failed-reopen restore never applies its configuration onto another revision', async () => {
    writeDraft({
      session: 's',
      project: { id: 'p1', revision: 7 },
      clean: '',
      info: { ...REQUEST.info, name: 'Restored name' },
      boards: draftBoards([blankBoard(0, 1, 'MDB')]),
      profileText: '',
      language: null,
      market: '',
      configuration: {
        doc: {
          ...STORED,
          strips: [
            {
              ...STORED.strips[0],
              designation: { function: null, location: null, product: 'XR7' },
            },
          ],
        },
        dropUnrecoverable: false,
      },
    });
    renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    const offer = await screen.findByTestId('draft-offer');
    api.open.mockResolvedValueOnce({ kind: 'failed' });
    fireEvent.click(within(offer).getByText('Restore unsaved work'));
    await screen.findByTestId('draft-project-gone');
    // Revision 1 is open; the copy was made against revision 7: not applied.
    expect(designation().value).toBe('X1');
  });

  it('setting a field back to exactly the stored (trimmed) value reads clean', async () => {
    renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    fireEvent.change(nameField(), { target: { value: '  Padded name  ' } });
    api.revise.mockResolvedValueOnce({ kind: 'saved', project: savedAs('Padded name', 'X1') });
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
    // What is sent (and stored) is trimmed.
    expect(api.revise.mock.calls[0]?.[0]?.request.info.name).toBe('Padded name');
    await waitFor(() => {
      expect(unsaved()).toBe(false);
    });
    fireEvent.change(nameField(), { target: { value: 'Edited' } });
    await waitFor(() => {
      expect(unsaved()).toBe(true);
    });
    // Back to exactly the stored value.
    fireEvent.change(nameField(), { target: { value: 'Padded name' } });
    await waitFor(() => {
      expect(unsaved()).toBe(false);
    });
  });
});
