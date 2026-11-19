import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import {
  clearDraft,
  draftBoards,
  readDraft,
  storedDrafts,
  QUARANTINE_KEY,
  deleteUnreadableCopy,
  unreadableCopies,
  UNREADABLE_KEY,
  writeDraft,
  DRAFT_STORAGE_KEY,
} from '@/components/design/draft';
import { blankBoard } from '@/components/design/schedule';
import type {
  approveRevision,
  BoardDesignResponse,
  designProject,
  exportDesign,
  listProjects,
  openProject,
  reviseProject,
  saveProject,
  SavedProject,
  SavedRequest,
} from '@/lib/design';

import type * as DesignModule from '@/lib/design';

import { asSignedIn, renderApp } from './helpers';

/**
 * M14: opening, creating and switching between projects. Unsaved work is
 * never discarded without an explicit choice; answers started for the project
 * left are let go; drafts are kept per identity and project. Mocked API: the
 * real API and database are covered by the browser run, not here.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  save: vi.fn<typeof saveProject>(),
  revise: vi.fn<typeof reviseProject>(),
  open: vi.fn<typeof openProject>(),
  approve: vi.fn<typeof approveRevision>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return {
    ...actual,
    listProjects: api.list,
    saveProject: api.save,
    reviseProject: api.revise,
    openProject: api.open,
    approveRevision: api.approve,
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

function saved(name: string, revision = 1): SavedProject {
  return {
    configuration_status: 'none',
    id: 'p1',
    name,
    revision,
    validated: false,
    export_status: 'not_validated',
    author_unknown: false,
    author_established: true,
    is_author: false,
    permissions: ['approve_revision', 'delete_project', 'edit_company_settings'],
    revisions: Array.from({ length: revision }, (_, index) => ({
      number: index + 1,
      note: '',
      author: 'e',
      approver_account_recorded: false,
      author_unknown: false,
      author_established: true,
      created_at: '2026-10-02T10:00:00Z',
      validated: false,
      export_status: 'not_validated' as const,
    })),
    request: { ...REQUEST, info: { ...REQUEST.info, name } },
    // Saved since revisions are validated on saving (Phase 1), with nothing blocking.
    ...({ validated: true, findings: [], snapshot_sha256: 'a'.repeat(64) } as object),
  };
}

const DESIGNED = {
  project: {
    info: { name: 'Tower' },
    profile: 'iec-default',
    parts: [],
    boards: [{ id: 'DB1', name: 'DB1', devices: [], cables: [], circuits: [], notes: [] }],
  },
  profile: { key: 'iec-default' },
} as unknown as BoardDesignResponse;

function renderScreen(
  extra: {
    leaveImpl?: (href: string) => void;
    designImpl?: typeof designProject;
    exportImpl?: typeof exportDesign;
    saveImpl?: (blob: Blob, filename: string) => void;
    /** Render as a signed-in account (approving needs one), not a trial. */
    account?: boolean;
  } = {},
) {
  const { account = false, ...impls } = extra;
  const screenUi = (
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      {...impls}
    />
  );
  return renderApp(account ? asSignedIn(screenUi) : screenUi);
}

/*
 * Queries are scoped to the part of the page they read: the whole workspace
 * is rendered, and a page-wide role or label query costs tens of
 * milliseconds a call in jsdom.
 */
const overview = () => within(screen.getByTestId('view-overview'));
const loadRow = () => within(screen.getByTestId('design-load-0'));
const projects = () => within(screen.getByTestId('projects-panel'));

/** An overview field (project details, approval) by its label. */
function overviewField(label: string) {
  return overview().getByLabelText<HTMLInputElement>(label);
}

function type(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

function designButton(): HTMLElement {
  return screen.getByText('Design the board', { selector: 'button' });
}

/** Enough for the form to be complete: a name and one described, powered load. */
async function completeForm(name = 'Tower') {
  await screen.findByTestId('projects-panel');
  type(overviewField('Project name'), name);
  type(loadRow().getByLabelText('Circuit'), 'Lights');
  type(loadRow().getByLabelText(/^Power.*kW/), '1');
}

beforeEach(() => {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: ['approve_revision', 'delete_project', 'edit_company_settings'],
      projects: [{ id: 'p1', name: 'Stored tower', revisions: 1, updated_at: 'now' }],
    },
  });
  api.open.mockResolvedValue({ kind: 'saved', project: saved('Stored tower') });
});

afterEach(() => {
  vi.clearAllMocks();
  // Answers queued with *Once and not used must not reach the next test.
  for (const mock of Object.values(api)) mock.mockReset();
  window.history.replaceState(null, '', '/');
});

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

/** Another saved project than `saved()`'s p1. */
function project(id: string, name: string, revision = 1): SavedProject {
  return { ...saved(name, revision), id };
}

const PERMISSIONS: ('approve_revision' | 'delete_project' | 'edit_company_settings')[] = [
  'approve_revision',
  'delete_project',
  'edit_company_settings',
];

const TWO = {
  kind: 'listed' as const,
  page: {
    permissions: PERMISSIONS,
    projects: [
      { id: 'p1', name: 'Stored tower', revisions: 1, updated_at: '2026-11-01T10:00:00Z' },
      { id: 'p2', name: 'Second plant', revisions: 1, updated_at: '2026-11-02T10:00:00Z' },
    ],
  },
};

function switchDialog() {
  return within(screen.getByTestId('switch-dialog').closest('dialog') as HTMLElement);
}

function switchAsking(): boolean {
  return screen.getByTestId('switch-dialog').closest('dialog')?.open ?? false;
}

async function openRow(id: string) {
  const row = await screen.findByTestId(`project-${id}`);
  fireEvent.click(within(row).getByText('Open'));
}

describe('the project list', () => {
  it('says it is loading, then that there are no projects', async () => {
    const pending = deferred<Awaited<ReturnType<typeof listProjects>>>();
    api.list.mockReturnValue(pending.promise);
    renderScreen();
    await screen.findByTestId('projects-panel');
    expect(await projects().findByTestId('projects-loading')).toBeTruthy();
    await act(async () => {
      pending.resolve({ kind: 'listed', page: { permissions: [], projects: [] } });
      await pending.promise;
    });
    expect(projects().getByTestId('projects-empty')).toBeTruthy();
    expect(projects().queryByTestId('projects-loading')).toBeNull();
  });

  it('offers a retry when the list fails, keeping what is typed', async () => {
    api.list.mockResolvedValueOnce({ kind: 'failed' });
    renderScreen();
    await completeForm('Typed tower');
    const retry = await projects().findByTestId('projects-retry');
    expect(overviewField('Project name').value).toBe('Typed tower');
    api.list.mockResolvedValue(TWO);
    fireEvent.click(retry);
    expect(await projects().findByTestId('project-p2')).toBeTruthy();
    expect(projects().queryByTestId('projects-list-error')).toBeNull();
    expect(overviewField('Project name').value).toBe('Typed tower');
  });

  it('shows each project with its revisions and save date, and marks the open one', async () => {
    api.list.mockResolvedValue(TWO);
    api.open.mockResolvedValue({ kind: 'saved', project: project('p2', 'Second plant') });
    renderScreen();
    const row = await screen.findByTestId('project-p2');
    expect(within(row).getByTestId('project-updated').textContent).toContain('2026');
    expect(row.getAttribute('aria-current')).toBeNull();
    await openRow('p2');
    await waitFor(() => {
      expect(screen.getByTestId('project-p2').getAttribute('aria-current')).toBe('true');
    });
    expect(screen.getByTestId('project-p1').getAttribute('aria-current')).toBeNull();
    expect(projects().getByTestId('project-current').textContent).toBe(
      'Current project: Second plant',
    );
  });

  it('pages on with Show more, keeping the projects already listed', async () => {
    api.list.mockResolvedValueOnce({ ...TWO, page: { ...TWO.page, next_cursor: 'c2' } });
    api.list.mockResolvedValueOnce({
      kind: 'listed',
      page: {
        permissions: [],
        projects: [{ id: 'p3', name: 'Third', revisions: 2, updated_at: '2026-10-01T10:00:00Z' }],
      },
    });
    renderScreen();
    await screen.findByTestId('projects-panel');
    fireEvent.click(await projects().findByTestId('projects-more'));
    expect(await screen.findByTestId('project-p3')).toBeTruthy();
    expect(screen.getByTestId('project-p1')).toBeTruthy();
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: 'c2' }));
    expect(projects().queryByTestId('projects-more')).toBeNull();
  });

  it('a project gone since the list was read is not opened, the list refreshes, the work stays', async () => {
    api.list.mockResolvedValue(TWO);
    api.open.mockResolvedValue({ kind: 'refused', code: 'project_not_found', detail: 'x' });
    renderScreen();
    await completeForm('Typed tower');
    await openRow('p2');
    await waitFor(() => {
      expect(switchAsking()).toBe(true);
    });
    fireEvent.click(switchDialog().getByTestId('switch-discard'));
    expect(await projects().findByTestId('projects-message')).toBeTruthy();
    expect(projects().getByTestId('projects-message').textContent).toContain('no longer available');
    // The discard applies only once the other project is there: nothing was lost.
    expect(overviewField('Project name').value).toBe('Typed tower');
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
    await waitFor(() => {
      expect(api.list.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });
});

describe('switching with unsaved work', () => {
  beforeEach(() => {
    api.list.mockResolvedValue(TWO);
  });

  it('opens straight away when nothing is unsaved', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: project('p2', 'Second plant') });
    renderScreen();
    await openRow('p2');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Second plant');
    });
    expect(switchAsking()).toBe(false);
  });

  it('Cancel keeps the project and everything typed', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: saved('Stored tower') });
    renderScreen();
    await openRow('p1');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Stored tower');
    });
    type(overviewField('Project name'), 'Edited tower');
    await openRow('p2');
    await waitFor(() => {
      expect(switchAsking()).toBe(true);
    });
    // The discard names the project it is about.
    expect(switchDialog().getByTestId('switch-discard').textContent).toBe(
      'Discard changes to Stored tower',
    );
    expect(switchDialog().getByTestId('switch-body').textContent).toContain('Second plant');
    fireEvent.click(switchDialog().getByTestId('switch-cancel'));
    expect(switchAsking()).toBe(false);
    expect(overviewField('Project name').value).toBe('Edited tower');
    expect(api.open).toHaveBeenCalledTimes(1);
  });

  it('Save stores the edits as a new revision, then opens the other project', async () => {
    api.open.mockResolvedValueOnce({ kind: 'saved', project: saved('Stored tower') });
    api.open.mockResolvedValueOnce({ kind: 'saved', project: project('p2', 'Second plant') });
    api.revise.mockResolvedValue({ kind: 'saved', project: saved('Edited tower', 2) });
    renderScreen();
    await openRow('p1');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Stored tower');
    });
    type(overviewField('Project name'), 'Edited tower');
    await openRow('p2');
    await waitFor(() => {
      expect(switchAsking()).toBe(true);
    });
    fireEvent.click(switchDialog().getByTestId('switch-save'));
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Second plant');
    });
    expect(api.revise).toHaveBeenCalledWith(expect.objectContaining({ id: 'p1' }));
    expect(api.open).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'p2' }));
  });

  it('a refused save keeps the project and all input, and does not switch', async () => {
    api.open.mockResolvedValueOnce({ kind: 'saved', project: saved('Stored tower') });
    api.revise.mockResolvedValue({
      kind: 'refused',
      code: 'validation_failed',
      detail: 'Refused for the test.',
    });
    renderScreen();
    await openRow('p1');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Stored tower');
    });
    type(overviewField('Project name'), 'Edited tower');
    await openRow('p2');
    await waitFor(() => {
      expect(switchAsking()).toBe(true);
    });
    fireEvent.click(switchDialog().getByTestId('switch-save'));
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.open).toHaveBeenCalledTimes(1);
    expect(overviewField('Project name').value).toBe('Edited tower');
    expect(projects().getByTestId('project-current').textContent).toBe(
      'Current project: Stored tower, with unsaved changes',
    );
  });

  it('New project asks first, then starts empty with nothing of the project left', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: saved('Stored tower') });
    renderScreen();
    await openRow('p1');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Stored tower');
    });
    type(overviewField('Project name'), 'Edited tower');
    fireEvent.click(projects().getByTestId('project-new'));
    await waitFor(() => {
      expect(switchAsking()).toBe(true);
    });
    expect(switchDialog().getByTestId('switch-body').textContent).toContain('a new project');
    fireEvent.click(switchDialog().getByTestId('switch-discard'));
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('');
    });
    expect(screen.queryByTestId('project-opened')).toBeNull();
    expect(projects().getByTestId('project-current').textContent).toBe(
      'Current project: a new project, not saved yet',
    );
    expect(screen.getByTestId('project-p1').getAttribute('aria-current')).toBeNull();
    expect(screen.queryByText('Unsaved changes')).toBeNull();
    await waitFor(() => {
      expect(document.activeElement).toBe(projects().getByTestId('project-current'));
    });
  });
});

describe('answers for the project left', () => {
  beforeEach(() => {
    api.list.mockResolvedValue(TWO);
  });

  it('a design answering after another project opened is not shown', async () => {
    const pending = deferred<Awaited<ReturnType<typeof designProject>>>();
    const designImpl = vi.fn<typeof designProject>().mockReturnValue(pending.promise);
    api.open.mockResolvedValueOnce({ kind: 'saved', project: saved('Stored tower') });
    api.open.mockResolvedValueOnce({ kind: 'saved', project: project('p2', 'Second plant') });
    renderScreen({ designImpl });
    await openRow('p1');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Stored tower');
    });
    fireEvent.click(designButton());
    expect(designImpl).toHaveBeenCalledTimes(1);
    await openRow('p2');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Second plant');
    });
    await act(async () => {
      pending.resolve({ kind: 'designed', response: DESIGNED });
      await pending.promise;
    });
    expect(screen.queryByTestId('design-result')).toBeNull();
  });

  it('A, B, then A again: designs answering late from earlier visits are not shown', async () => {
    const forA = deferred<Awaited<ReturnType<typeof designProject>>>();
    const forB = deferred<Awaited<ReturnType<typeof designProject>>>();
    const designImpl = vi
      .fn<typeof designProject>()
      .mockReturnValueOnce(forA.promise)
      .mockReturnValueOnce(forB.promise);
    api.open
      .mockResolvedValueOnce({ kind: 'saved', project: saved('Stored tower') })
      .mockResolvedValueOnce({ kind: 'saved', project: project('p2', 'Second plant') })
      .mockResolvedValueOnce({ kind: 'saved', project: saved('Stored tower') });
    renderScreen({ designImpl });
    await openRow('p1');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Stored tower');
    });
    fireEvent.click(designButton());
    await openRow('p2');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Second plant');
    });
    fireEvent.click(designButton());
    await openRow('p1');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Stored tower');
    });
    // B's design answers, then A's first one: both belong to visits since left.
    await act(async () => {
      forB.resolve({ kind: 'designed', response: DESIGNED });
      await forB.promise;
    });
    await act(async () => {
      forA.resolve({ kind: 'designed', response: DESIGNED });
      await forA.promise;
    });
    expect(screen.queryByTestId('design-result')).toBeNull();
    expect(overviewField('Project name').value).toBe('Stored tower');
  });

  it('while an open is answering, the list waits (no second open overlaps it)', async () => {
    const pending = deferred<Awaited<ReturnType<typeof openProject>>>();
    api.open.mockReturnValueOnce(pending.promise);
    renderScreen();
    await openRow('p1');
    const rowB = screen.getByTestId('project-p2');
    expect(within(rowB).getByText<HTMLButtonElement>('Open').disabled).toBe(true);
    expect(projects().getByTestId<HTMLButtonElement>('project-new').disabled).toBe(true);
    await act(async () => {
      pending.resolve({ kind: 'saved', project: saved('Stored tower') });
      await pending.promise;
    });
    expect(within(rowB).getByText<HTMLButtonElement>('Open').disabled).toBe(false);
  });
});

describe('drafts kept per identity and project', () => {
  const body = (session: string, projectId: string | null, name: string) => ({
    session,
    project: projectId === null ? null : { id: projectId, revision: 1 },
    clean: '',
    info: { name, number: '', customer: '', consultant: '', contractor: '' },
    boards: draftBoards([blankBoard(0, 1, 'DB1')]),
    profileText: '',
    language: null,
    market: '',
  });

  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("writing one project's draft leaves another's, and a new project has its own", () => {
    expect(writeDraft(body('s', 'p1', 'A'), 't1')).toBe(true);
    expect(writeDraft(body('s', 'p2', 'B'), 't1')).toBe(true);
    expect(writeDraft(body('s', null, 'New'), 't1')).toBe(true);
    expect(readDraft('s', 'p1')?.info.name).toBe('A');
    expect(readDraft('s', 'p2')?.info.name).toBe('B');
    expect(readDraft('s', null)?.info.name).toBe('New');
    clearDraft('s', 'p2');
    expect(readDraft('s', 'p2')).toBeNull();
    expect(readDraft('s', 'p1')?.info.name).toBe('A');
  });

  it('is not offered under another tenant, nor to another identity (which removes it)', () => {
    writeDraft(body('s', 'p1', 'A'), 't1');
    expect(readDraft('s', 'p1', 't2')).toBeNull();
    expect(readDraft('s', 'p1', 't1')?.info.name).toBe('A');
    expect(readDraft('other', 'p1')).toBeNull();
    expect(storedDrafts()).toHaveLength(0);
  });

  it("a draft in the earlier format is read as its project's and kept beside new ones", () => {
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({ v: 1, ...body('s', 'p1', 'Old') }),
    );
    writeDraft(body('s', 'p2', 'B'));
    expect(readDraft('s', 'p1')?.info.name).toBe('Old');
    expect(readDraft('s', 'p2')?.info.name).toBe('B');
  });

  it('data that cannot be read is moved aside, not overwritten', () => {
    window.sessionStorage.setItem(DRAFT_STORAGE_KEY, '{"v":7,"something":"else"}');
    writeDraft(body('s', 'p1', 'A'));
    expect(window.sessionStorage.getItem(UNREADABLE_KEY)).toBe('{"v":7,"something":"else"}');
    expect(readDraft('s', 'p1')?.info.name).toBe('A');
  });

  it("opening a project offers its own kept draft, never another project's", async () => {
    api.list.mockResolvedValue(TWO);
    api.open.mockResolvedValue({ kind: 'saved', project: project('p2', 'Second plant') });
    writeDraft(body('s', 'p1', 'Kept for p1'));
    renderScreen();
    // On arrival, the most recent draft is offered; it is p1's.
    const offer = await screen.findByTestId('draft-offer');
    fireEvent.click(within(offer).getByText('Discard it'));
    writeDraft(body('s', 'p2', 'Kept for p2'));
    await openRow('p2');
    const p2Offer = await screen.findByTestId('draft-offer');
    fireEvent.click(within(p2Offer).getByText('Restore unsaved work'));
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Kept for p2');
    });
    expect(readDraft('s', 'p1')).toBeNull();
  });

  it('on arrival every kept copy is listed by project; dismissing one removes only that one', async () => {
    api.list.mockResolvedValue(TWO);
    writeDraft(body('s', null, 'Kept for a new project'));
    await new Promise((resolve) => setTimeout(resolve, 2));
    writeDraft(body('s', 'p1', 'Kept for p1'));
    renderScreen();
    await screen.findByTestId('draft-offer');
    const rows = () => screen.getAllByTestId('draft-copy');
    expect(rows()).toHaveLength(2);
    // Most recent first, each named by its project.
    expect(rows()[0]?.textContent).toContain('Kept for p1');
    expect(rows()[1]?.textContent).toContain('a new project');
    fireEvent.click(within(rows()[0] as HTMLElement).getByText('Discard it'));
    expect(readDraft('s', 'p1')).toBeNull();
    expect(readDraft('s', null)?.info.name).toBe('Kept for a new project');
    expect(rows()).toHaveLength(1);
    fireEvent.click(within(rows()[0] as HTMLElement).getByText('Restore unsaved work'));
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Kept for a new project');
    });
  });

  it("opening another project replaces an offer of a different project's draft, keeping it", async () => {
    api.list.mockResolvedValue(TWO);
    api.open
      .mockResolvedValueOnce({ kind: 'saved', project: project('p2', 'Second plant') })
      .mockResolvedValueOnce({ kind: 'saved', project: saved('Stored tower') });
    writeDraft(body('s', 'p1', 'Kept p1 work'));
    renderScreen();
    const offer = await screen.findByTestId('draft-offer');
    // The offer names the project it belongs to.
    expect(within(offer).getByTestId('draft-offer-project').textContent).toContain('Kept p1 work');
    await openRow('p2');
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Second plant');
    });
    // p2 has no draft: nothing is offered on it, and p1's draft stays kept.
    expect(screen.queryByTestId('draft-offer')).toBeNull();
    expect(readDraft('s', 'p1')?.info.name).toBe('Kept p1 work');
    // Edits to p2 are kept in p2's own slot meanwhile.
    type(overviewField('Project name'), 'Second plant edited');
    await waitFor(() => {
      expect(readDraft('s', 'p2')?.info.name).toBe('Second plant edited');
    });
    expect(readDraft('s', 'p1')?.info.name).toBe('Kept p1 work');
  });

  it("work typed while another project's draft is offered is kept in its own slot", async () => {
    api.list.mockResolvedValue(TWO);
    writeDraft(body('s', 'p1', 'Kept p1 work'));
    renderScreen();
    await screen.findByTestId('draft-offer');
    type(overviewField('Project name'), 'Typed while offered');
    await waitFor(() => {
      expect(readDraft('s', null)?.info.name).toBe('Typed while offered');
    });
    expect(readDraft('s', 'p1')?.info.name).toBe('Kept p1 work');
  });

  it('an offer for one project is not shown in another or in a new project, and stays kept', async () => {
    api.list.mockResolvedValue(TWO);
    api.open.mockResolvedValue({ kind: 'saved', project: project('p2', 'Second plant') });
    writeDraft(body('s', 'p1', 'Kept p1 work'));
    renderScreen();
    await screen.findByTestId('draft-offer');
    // The list says which projects have a kept copy.
    expect(within(screen.getByTestId('project-p1')).getByTestId('project-has-kept')).toBeTruthy();
    await openRow('p2');
    await waitFor(
      () => {
        expect(overviewField('Project name').value).toBe('Second plant');
      },
      { timeout: 5000 },
    );
    expect(screen.queryByTestId('draft-offer')).toBeNull();
    fireEvent.click(projects().getByTestId('project-new'));
    await waitFor(
      () => {
        expect(overviewField('Project name').value).toBe('');
      },
      { timeout: 5000 },
    );
    expect(screen.queryByTestId('draft-offer')).toBeNull();
    expect(readDraft('s', 'p1')?.info.name).toBe('Kept p1 work');
  });

  it('typing while an older copy is offered keeps a second copy; each is restored or dismissed alone', async () => {
    api.list.mockResolvedValue(TWO);
    writeDraft(body('s', null, 'Older copy'));
    const first = renderScreen();
    await screen.findByTestId('draft-offer');
    type(overviewField('Project name'), 'Newer typing');
    await waitFor(() => {
      expect(storedDrafts()).toHaveLength(2);
    });
    const names = storedDrafts().map((kept) => kept.info.name);
    expect(names).toContain('Older copy');
    expect(names).toContain('Newer typing');
    // The older copy is still offered (the newer one is the page's own).
    expect(screen.getAllByTestId('draft-copy')).toHaveLength(1);
    expect(screen.getByTestId('draft-copy').textContent).toContain('a new project');
    // A reload: both copies are offered, each named and timed.
    first.unmount();
    renderScreen();
    await screen.findByTestId('draft-offer');
    const rows = screen.getAllByTestId('draft-copy');
    expect(rows).toHaveLength(2);
    for (const row of rows) expect(row.textContent).toMatch(/Kept /);
    // Restore the older one explicitly: the newer one stays kept.
    const olderIndex = storedDrafts().findIndex((kept) => kept.info.name === 'Older copy');
    fireEvent.click(within(rows[olderIndex] as HTMLElement).getByText('Restore unsaved work'));
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Older copy');
    });
    expect(storedDrafts().map((kept) => kept.info.name)).toContain('Newer typing');
  });

  it('when both copies are taken, typing says the work is not kept, and nothing is overwritten', async () => {
    api.list.mockResolvedValue(TWO);
    writeDraft(body('s', null, 'Copy one'), null, 1);
    writeDraft(body('s', null, 'Copy two'), null, 2);
    renderScreen();
    await screen.findByTestId('draft-offer');
    type(overviewField('Project name'), 'Third typing');
    expect((await screen.findByTestId('draft-not-kept')).textContent).toContain(
      'two unsaved copies',
    );
    expect(
      storedDrafts()
        .map((kept) => kept.info.name)
        .sort(),
    ).toEqual(['Copy one', 'Copy two']);
  });

  it('when storage fails, the page says the work is not kept, and other drafts stay', async () => {
    api.list.mockResolvedValue(TWO);
    writeDraft(body('s', 'p1', 'Kept p1 work'));
    renderScreen();
    await screen.findByTestId('draft-offer');
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    try {
      type(overviewField('Project name'), 'Typed when full');
      expect((await screen.findByTestId('draft-not-kept')).textContent).toContain(
        'could not be kept',
      );
    } finally {
      setItem.mockRestore();
    }
    expect(readDraft('s', 'p1')?.info.name).toBe('Kept p1 work');
  });

  it("another identity's draft (current or earlier format) is quarantined: not offered, not shown, not deleted", async () => {
    api.list.mockResolvedValue(TWO);
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({ v: 1, ...body('account:someone-else', 'p1', 'Secret of another') }),
    );
    renderScreen();
    await screen.findByTestId('projects-panel');
    await waitFor(() => {
      expect(window.sessionStorage.getItem(QUARANTINE_KEY)).toContain('other-identity');
    });
    expect(screen.queryByTestId('draft-offer')).toBeNull();
    expect(document.body.textContent).not.toContain('Secret of another');
    expect(storedDrafts()).toHaveLength(0);
  });

  it('a restore whose open answers after another project was opened is let go', async () => {
    api.list.mockResolvedValue(TWO);
    const late = deferred<Awaited<ReturnType<typeof openProject>>>();
    writeDraft(body('s', 'p1', 'Kept p1 work'));
    renderScreen();
    const offer = await screen.findByTestId('draft-offer');
    api.open.mockReturnValueOnce(late.promise);
    fireEvent.click(within(offer).getByText('Restore unsaved work'));
    api.open.mockResolvedValueOnce({ kind: 'saved', project: project('p2', 'Second plant') });
    // The list is not disabled by a restore: open p2 meanwhile.
    fireEvent.click(within(screen.getByTestId('project-p2')).getByText('Open'));
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Second plant');
    });
    await act(async () => {
      late.resolve({ kind: 'saved', project: saved('Stored tower') });
      await late.promise;
    });
    expect(overviewField('Project name').value).toBe('Second plant');
    expect(readDraft('s', 'p1')?.info.name).toBe('Kept p1 work');
  });

  it("a trial's drafts are not offered to the account it signs in to; they are quarantined, not deleted", () => {
    writeDraft(body('trial-session', 'p1', 'Typed in the trial'), 'trial-tenant');
    expect(readDraft('account:user-1', undefined, 'account-tenant')).toBeNull();
    expect(storedDrafts()).toHaveLength(0);
    expect(window.sessionStorage.getItem(QUARANTINE_KEY)).toContain('other-identity');
  });

  it('quarantine is settled once: repeated reads neither duplicate entries nor push others out', async () => {
    window.sessionStorage.setItem(
      QUARANTINE_KEY,
      JSON.stringify([{ reason: 'other-identity', at: 1, raw: 'earlier quarantined entry' }]),
    );
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({ v: 2, drafts: { 's|p1': { broken: true } } }),
    );
    api.list.mockResolvedValue(TWO);
    renderScreen();
    await screen.findByTestId('projects-panel');
    for (let i = 0; i < 12; i += 1) type(overviewField('Project name'), `Typed ${String(i)}`);
    await waitFor(() => {
      expect(readDraft('s', null)?.info.name).toBe('Typed 11');
    });
    const list = JSON.parse(window.sessionStorage.getItem(QUARANTINE_KEY) ?? '[]') as {
      raw: string;
    }[];
    expect(list.some((entry) => entry.raw === 'earlier quarantined entry')).toBe(true);
    expect(list.filter((entry) => entry.raw.includes('broken'))).toHaveLength(1);
  });

  it('when the quarantine cannot be written, nothing is removed from the drafts', () => {
    writeDraft(body('account:someone-else', 'p1', 'Theirs'));
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem');
    try {
      expect(readDraft('s', 'p1')).toBeNull();
      expect(removeItem).not.toHaveBeenCalled();
    } finally {
      setItem.mockRestore();
      removeItem.mockRestore();
    }
    // Not offered to 's', and still kept (not deleted) since it could not be set aside.
    expect(storedDrafts().map((kept) => kept.info.name)).toEqual(['Theirs']);
  });

  it('an unreadable value is never written over before it is set aside', () => {
    window.sessionStorage.setItem(UNREADABLE_KEY, 'set aside earlier');
    window.sessionStorage.setItem(DRAFT_STORAGE_KEY, 'not a draft store');
    // Reading changes nothing stored.
    expect(readDraft('s', 'p1')).toBeNull();
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).toBe('not a draft store');
    // A write cannot set it aside (storage full): nothing is written over it.
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    try {
      expect(writeDraft(body('s', 'p1', 'A'))).toBe(false);
    } finally {
      setItem.mockRestore();
    }
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).toBe('not a draft store');
    // With room, it goes to quarantine (the unreadable slot already holds other data).
    expect(writeDraft(body('s', 'p1', 'A'))).toBe(true);
    expect(window.sessionStorage.getItem(UNREADABLE_KEY)).toBe('set aside earlier');
    expect(window.sessionStorage.getItem(QUARANTINE_KEY)).toContain('not a draft store');
    expect(readDraft('s', 'p1')?.info.name).toBe('A');
  });

  it('a corrupt copy that cannot be set aside is never written over by the same slot', () => {
    const corrupt = { broken: true, note: '<img src=x onerror=alert(1)>' };
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({
        v: 2,
        drafts: { 's|new': corrupt, 's|p2': { ...body('s', 'p2', 'B'), savedAt: 5 } },
      }),
    );
    const before = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
    const real = window.sessionStorage.setItem.bind(window.sessionStorage);
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation((key, value) => {
      // Storage full: the quarantine (and the unreadable slot) cannot take more.
      if (key !== DRAFT_STORAGE_KEY) throw new DOMException('full', 'QuotaExceededError');
      real(key, value);
    });
    try {
      expect(writeDraft(body('s', null, 'Typed now'))).toBe(false);
      // Repeated attempts change nothing either.
      expect(writeDraft(body('s', null, 'Typed again'))).toBe(false);
    } finally {
      setItem.mockRestore();
    }
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).toBe(before);
  });

  /** Storage that has no room beside the draft key (quarantine and unreadable slot refuse). */
  function fullBesideDrafts() {
    const store = window.sessionStorage;
    const real = store.setItem.bind(store);
    return vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation((key: string, value: string) => {
        if (key !== DRAFT_STORAGE_KEY) throw new DOMException('full', 'QuotaExceededError');
        real(key, value);
      });
  }
  /** The stored draft entry in one slot. */
  function slotOf(slot: string): unknown {
    const value = JSON.parse(window.sessionStorage.getItem(DRAFT_STORAGE_KEY) ?? '{}') as {
      drafts?: Record<string, unknown>;
    };
    return value.drafts?.[slot];
  }

  const CORRUPT = { broken: true, note: '<img src=x onerror="window.__pwned=1">' };
  function seedCorruptNew() {
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({
        v: 2,
        drafts: {
          's|new': CORRUPT,
          // The other copy of the new project is taken: only the unreadable slot is left.
          's|new|2': { ...body('s', null, 'Other new copy'), savedAt: 4 },
          's|p2': { ...body('s', 'p2', 'B kept'), savedAt: 5 },
        },
      }),
    );
  }
  const p2Bytes = () =>
    JSON.stringify(
      (
        JSON.parse(window.sessionStorage.getItem(DRAFT_STORAGE_KEY) ?? '{}') as {
          drafts?: Record<string, unknown>;
        }
      ).drafts?.['s|p2'],
    );

  it('blocked: edits stay on the page, are not offered as recoverable, and the corrupt copy survives reloads', async () => {
    api.list.mockResolvedValue(TWO);
    seedCorruptNew();
    const p2 = p2Bytes();
    const full = fullBesideDrafts();
    try {
      const first = renderScreen();
      await screen.findByTestId('projects-panel');
      type(overviewField('Project name'), 'Typed while blocked');
      const warning = await screen.findByTestId('draft-not-kept');
      expect(warning.getAttribute('role')).toBe('alert');
      expect(warning.textContent).toContain('not being kept');
      // Repeated attempts (more typing) still write nothing over it.
      type(overviewField('Project name'), 'Typed again while blocked');
      expect(overviewField('Project name').value).toBe('Typed again while blocked');
      expect(slotOf('s|new')).toEqual(CORRUPT);
      expect(p2Bytes()).toBe(p2);
      // Not offered as recoverable anywhere; its contents never reach the page.
      expect(document.body.innerHTML).not.toContain('onerror');
      expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
      first.unmount();
      // A reload: still blocked, still nothing written over it.
      renderScreen();
      await screen.findByTestId('projects-panel');
      type(overviewField('Project name'), 'After reload');
      await screen.findByTestId('draft-not-kept');
      expect(slotOf('s|new')).toEqual(CORRUPT);
      expect(p2Bytes()).toBe(p2);
    } finally {
      full.mockRestore();
    }
  });

  it('deleting the unreadable copy asks first; Cancel changes nothing; confirming deletes only it and keeps the work', async () => {
    api.list.mockResolvedValue(TWO);
    seedCorruptNew();
    const p2 = p2Bytes();
    const full = fullBesideDrafts();
    try {
      renderScreen();
      await screen.findByTestId('projects-panel');
      type(overviewField('Project name'), 'Work to keep');
      fireEvent.click(await screen.findByTestId('draft-delete-unreadable'));
      const body = () => screen.getByTestId('draft-delete-body');
      expect(body().textContent).toContain('unreadable local copy 1');
      expect(body().textContent).toContain('new project');
      fireEvent.click(screen.getByTestId('draft-delete-cancel'));
      expect(slotOf('s|new')).toEqual(CORRUPT);
      fireEvent.click(screen.getByTestId('draft-delete-unreadable'));
      fireEvent.click(screen.getByTestId('draft-delete-confirm'));
      await waitFor(() => {
        expect(readDraft('s', null)?.info.name).toBe('Work to keep');
      });
      expect(screen.queryByTestId('draft-not-kept')).toBeNull();
      // The asking button is gone: focus and the announcement go to the status.
      await waitFor(() => {
        expect(document.activeElement).toBe(screen.getByTestId('draft-deleted'));
      });
      expect(screen.getByTestId('draft-deleted').getAttribute('role')).toBe('status');
      expect(p2Bytes()).toBe(p2);
    } finally {
      full.mockRestore();
    }
  });

  it('set-aside data is only counted, and deleted only after confirmation, never shown', async () => {
    api.list.mockResolvedValue(TWO);
    window.sessionStorage.setItem(
      QUARANTINE_KEY,
      JSON.stringify([{ reason: 'other-identity', at: 1, raw: 'Secret of another account' }]),
    );
    writeDraft(body('s', 'p2', 'B kept'));
    const kept = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
    renderScreen();
    const line = await screen.findByTestId('draft-set-aside');
    expect(line.textContent).toContain('1 item');
    expect(document.body.textContent).not.toContain('Secret of another account');
    fireEvent.click(screen.getByTestId('draft-clear-aside'));
    fireEvent.click(screen.getByTestId('draft-delete-cancel'));
    expect(window.sessionStorage.getItem(QUARANTINE_KEY)).not.toBeNull();
    fireEvent.click(screen.getByTestId('draft-clear-aside'));
    fireEvent.click(screen.getByTestId('draft-delete-confirm'));
    await waitFor(() => {
      expect(window.sessionStorage.getItem(QUARANTINE_KEY)).toBeNull();
    });
    expect(screen.queryByTestId('draft-set-aside')).toBeNull();
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).toBe(kept);
  });

  it("recovery actions never reach another identity's slot", () => {
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({ v: 2, drafts: { 'account:other|new': CORRUPT, 's|new': CORRUPT } }),
    );
    expect(unreadableCopies('s')).toEqual([{ project: null, copy: 1 }]);
    expect(deleteUnreadableCopy('s', null, 1)).toBe(true);
    const left = JSON.parse(window.sessionStorage.getItem(DRAFT_STORAGE_KEY) ?? '{}') as {
      drafts: Record<string, unknown>;
    };
    expect(Object.keys(left.drafts)).toEqual(['account:other|new']);
    expect(deleteUnreadableCopy('s', null, 1)).toBe(false);
  });

  it('when storage cannot be read, nothing is written over it', () => {
    writeDraft(body('s', 'p2', 'B kept'));
    const kept = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    try {
      expect(writeDraft(body('s', 'p1', 'A'))).toBe(false);
      clearDraft('s', 'p2');
    } finally {
      getItem.mockRestore();
    }
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).toBe(kept);
  });

  it('an unreadable copy is left alone while the other copy slot is free: the work is kept there', async () => {
    api.list.mockResolvedValue(TWO);
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({ v: 2, drafts: { 's|new': CORRUPT } }),
    );
    const full = fullBesideDrafts();
    try {
      renderScreen();
      await screen.findByTestId('projects-panel');
      type(overviewField('Project name'), 'Kept in the free copy');
      await waitFor(() => {
        expect(readDraft('s', null)?.info.name).toBe('Kept in the free copy');
      });
      expect(slotOf('s|new')).toEqual(CORRUPT);
      expect(screen.queryByTestId('draft-not-kept')).toBeNull();
    } finally {
      full.mockRestore();
    }
  });

  it('a failed read of the set-aside keys never authorizes overwriting them', () => {
    window.sessionStorage.setItem(UNREADABLE_KEY, 'earlier unreadable data');
    window.sessionStorage.setItem(
      QUARANTINE_KEY,
      JSON.stringify([{ reason: 'x', at: 1, raw: 'earlier entry' }]),
    );
    const realGet = window.sessionStorage.getItem.bind(window.sessionStorage);
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation((key: string) => {
      if (key === UNREADABLE_KEY || key === QUARANTINE_KEY)
        throw new DOMException('blocked', 'SecurityError');
      return realGet(key);
    });
    try {
      // A whole unreadable value cannot be set aside: the write is refused.
      window.sessionStorage.setItem(DRAFT_STORAGE_KEY, 'not a draft store');
      expect(writeDraft(body('s', 'p1', 'A'))).toBe(false);
      // An unreadable entry in the slot cannot be quarantined: the slot is not written.
      window.sessionStorage.setItem(
        DRAFT_STORAGE_KEY,
        JSON.stringify({ v: 2, drafts: { 's|p1': { broken: true } } }),
      );
      expect(writeDraft(body('s', 'p1', 'A'))).toBe(false);
    } finally {
      getItem.mockRestore();
    }
    expect(window.sessionStorage.getItem(UNREADABLE_KEY)).toBe('earlier unreadable data');
    expect(window.sessionStorage.getItem(QUARANTINE_KEY)).toContain('earlier entry');
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).toContain('broken');
  });
});
