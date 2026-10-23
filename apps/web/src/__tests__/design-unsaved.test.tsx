import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { leavingHref } from '@/components/design/workspace';
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

import { renderApp } from './helpers';

/**
 * Unsaved work and stale results on `/design`: what a save marks as saved,
 * what asks before discarding edits, and what a design no longer matching
 * its inputs may still be used for.
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
    id: 'p1',
    name,
    revision,
    validated: false,
    export_status: 'not_validated',
    revisions: Array.from({ length: revision }, (_, index) => ({
      number: index + 1,
      note: '',
      author: 'e',
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
  } = {},
) {
  renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      {...extra}
    />,
  );
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

/** The discard-changes dialog's footer, and whether the dialog is open. */
function dialog() {
  return within(screen.getByTestId('unsaved-dialog'));
}

function asking(): boolean {
  return screen.getByTestId('unsaved-dialog').closest('dialog')?.open ?? false;
}

async function waitForAsking() {
  await waitFor(() => {
    expect(asking()).toBe(true);
  });
}

function railLink(label: string): HTMLElement {
  const link = screen.getByText(label).closest('a');
  if (link === null) throw new Error(`no link ${label}`);
  return link;
}

function viewTab(label: string): HTMLElement {
  const tab = screen
    .getByText(label, { selector: '[role="tab"], [role="tab"] *' })
    .closest('[role="tab"]');
  if (!(tab instanceof HTMLElement)) throw new Error(`no tab ${label}`);
  return tab;
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

async function openStored() {
  const row = await screen.findByTestId('project-p1');
  fireEvent.click(within(row).getByText('Open'));
  await waitFor(() => {
    expect(overviewField('Project name').value).toBe('Stored tower');
  });
  return row;
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

beforeEach(() => {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: { projects: [{ id: 'p1', name: 'Stored tower', revisions: 1, updated_at: 'now' }] },
  });
  api.open.mockResolvedValue({ kind: 'saved', project: saved('Stored tower') });
});

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/');
});

describe('saving', () => {
  it('marks only what was sent as saved; an edit made while saving stays unsaved', async () => {
    const pending = deferred<Awaited<ReturnType<typeof saveProject>>>();
    api.save.mockReturnValue(pending.promise);
    renderScreen();
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    expect(await screen.findByText('Saving…')).toBeTruthy();
    expect(api.save.mock.calls[0]?.[0].name).toBe('Tower');

    type(overviewField('Customer'), 'Typed while saving');
    await act(async () => {
      pending.resolve({ kind: 'saved', project: saved('Tower') });
      await pending.promise;
    });
    expect(await screen.findByText('Saved as revision 1.')).toBeTruthy();
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
    expect(overviewField('Customer').value).toBe('Typed while saving');

    // Undoing the edit returns to exactly what was stored.
    type(overviewField('Customer'), '');
    await waitFor(() => {
      expect(screen.queryByText('Unsaved changes')).toBeNull();
    });
  });

  it('keeps every input when a save fails', async () => {
    api.save.mockResolvedValue({ kind: 'failed' });
    renderScreen();
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    expect(await screen.findByText('Save failed')).toBeTruthy();
    expect(projects().getByTestId('projects-message').textContent).toBe(
      'The project could not be saved. What you entered is still here; try again.',
    );
    expect(overviewField('Project name').value).toBe('Tower');
  });
});

describe('the unsaved changes guard', () => {
  it('asks before opening a saved project over unsaved edits', async () => {
    renderScreen();
    await completeForm('Edited tower');
    const row = await screen.findByTestId('project-p1');
    fireEvent.click(within(row).getByText('Open'));
    await waitForAsking();
    expect(api.open).not.toHaveBeenCalled();

    fireEvent.click(dialog().getByText('Keep editing'));
    expect(overviewField('Project name').value).toBe('Edited tower');

    fireEvent.click(within(row).getByText('Open'));
    fireEvent.click(dialog().getByText('Discard changes'));
    await waitFor(() => {
      expect(overviewField('Project name').value).toBe('Stored tower');
    });
  });

  it('opens a project straight away when nothing is unsaved', async () => {
    renderScreen();
    await openStored();
    expect(asking()).toBe(false);
  });

  it('holds a link out of the workspace until the engineer chooses', async () => {
    const leaveImpl = vi.fn();
    renderScreen({ leaveImpl });
    await completeForm();
    fireEvent.click(railLink('Calculate'));
    await waitForAsking();
    expect(leaveImpl).not.toHaveBeenCalled();

    fireEvent.click(dialog().getByText('Keep editing'));
    expect(leaveImpl).not.toHaveBeenCalled();
    expect(overviewField('Project name').value).toBe('Tower');

    fireEvent.click(railLink('Calculate'));
    fireEvent.click(dialog().getByText('Discard changes'));
    expect(leaveImpl).toHaveBeenCalledWith(`${window.location.origin}/calc`);
  });

  it('asks on Back out of the page, and stays when told to', async () => {
    // A fresh entry to land on: jsdom keeps one history across tests.
    window.history.pushState(null, '', '/design');
    renderScreen();
    await completeForm();
    // Dirty work puts a buffer entry for this page above the one it opened on.
    await waitFor(() => {
      expect(window.history.state).toMatchObject({ __panelpilotWorkspace: true });
    });
    act(() => {
      window.history.back();
    });
    await waitForAsking();
    fireEvent.click(dialog().getByText('Keep editing'));
    expect(window.location.pathname).toBe('/design');
    expect(window.history.state).toMatchObject({ __panelpilotWorkspace: true });
    expect(overviewField('Project name').value).toBe('Tower');
  });

  it('only treats a plain same-origin link to another page as leaving', () => {
    window.history.replaceState(null, '', '/design?view=review');
    function click(href: string, init: MouseEventInit = {}, attrs: Record<string, string> = {}) {
      const link = document.createElement('a');
      link.href = href;
      for (const [key, value] of Object.entries(attrs)) link.setAttribute(key, value);
      document.body.appendChild(link);
      const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...init });
      Object.defineProperty(event, 'target', { value: link });
      const result = leavingHref(event);
      link.remove();
      return result;
    }
    expect(click('/calc')).toBe(`${window.location.origin}/calc`);
    expect(click('/design?view=review#top')).toBeNull();
    expect(click('https://example.com/x')).toBeNull();
    expect(click('/calc', { ctrlKey: true })).toBeNull();
    expect(click('/calc', {}, { target: '_blank' })).toBeNull();
    expect(click('/calc', {}, { download: '' })).toBeNull();
  });
});

describe('views in the history', () => {
  it('gives each view its own entry, so Back returns to the previous view', async () => {
    window.history.replaceState(null, '', '/design');
    renderScreen();
    fireEvent.click(viewTab('Drawings'));
    fireEvent.click(viewTab('Materials'));
    expect(window.location.search).toBe('?view=materials');
    act(() => {
      window.history.back();
    });
    await waitFor(() => {
      expect(viewTab('Drawings').getAttribute('aria-selected')).toBe('true');
    });
    expect(window.location.search).toBe('?view=drawings');
  });
});

describe('a design the inputs have moved on from', () => {
  it('says so, and neither exports nor prices it until designed again', async () => {
    const designImpl = vi
      .fn<typeof designProject>()
      .mockResolvedValue({ kind: 'designed', response: DESIGNED });
    const exportImpl = vi
      .fn<typeof exportDesign>()
      .mockResolvedValue({ kind: 'exported', blob: new Blob(['x']), filename: 'Tower.pdf' });
    renderScreen({ designImpl, exportImpl, saveImpl: vi.fn() });
    await completeForm();
    fireEvent.click(designButton());
    await screen.findByTestId('design-result');
    expect(screen.getByTestId('design-export-pdf')).toBeTruthy();
    expect(screen.queryByTestId('design-stale')).toBeNull();

    type(loadRow().getByLabelText(/^Power.*kW/), '9');
    expect(await screen.findByTestId('design-stale')).toBeTruthy();
    expect(screen.queryByTestId('design-export-pdf')).toBeNull();
    // The sheets stay for reference, under the warning.
    expect(screen.getByTestId('design-result')).toBeTruthy();

    // Back to what was designed: current again, with nothing redesigned.
    type(loadRow().getByLabelText(/^Power.*kW/), '1');
    await waitFor(() => {
      expect(screen.queryByTestId('design-stale')).toBeNull();
    });
    fireEvent.click(screen.getByTestId('design-export-pdf'));
    await waitFor(() => {
      expect(exportImpl).toHaveBeenCalledTimes(1);
    });
    expect(designImpl).toHaveBeenCalledTimes(1);
  });
});

describe('approval', () => {
  it('cannot approve the stored revision while the page has unsaved changes', async () => {
    renderScreen();
    await openStored();
    type(overviewField('Approving engineer'), 'A. Engineer');
    const approve = projects().getByText<HTMLButtonElement>('Approve revision 1');
    expect(approve.disabled).toBe(false);

    type(overviewField('Customer'), 'Changed');
    expect(approve.disabled).toBe(true);
    expect(screen.getByTestId('approve-dirty').textContent).toContain('stored revision 1');
    fireEvent.click(approve);
    expect(api.approve).not.toHaveBeenCalled();
  });

  it('takes a design made before the approval as out of date, since its title block is', async () => {
    const approved = saved('Stored tower');
    approved.request = {
      ...approved.request,
      info: {
        ...approved.request.info,
        revisions: [
          {
            index: '01',
            date: '2026-10-02',
            description: 'Issue',
            drawn_by: '',
            checked_by: '',
            approved_by: 'A. Engineer',
          },
        ],
      },
    };
    approved.revisions = approved.revisions.map((entry) => ({
      ...entry,
      approved_by: 'A. Engineer',
      approved_at: '2026-10-03T10:00:00Z',
    }));
    api.approve.mockResolvedValue({ kind: 'saved', project: approved });
    const designImpl = vi
      .fn<typeof designProject>()
      .mockResolvedValue({ kind: 'designed', response: DESIGNED });
    renderScreen({ designImpl });
    await openStored();
    fireEvent.click(designButton());
    await screen.findByTestId('design-result');
    expect(screen.queryByTestId('design-stale')).toBeNull();

    type(overviewField('Approving engineer'), 'A. Engineer');
    fireEvent.click(projects().getByText('Approve revision 1'));
    expect(await screen.findByTestId('design-stale')).toBeTruthy();
    expect(screen.queryByTestId('design-export-pdf')).toBeNull();
  });
});
