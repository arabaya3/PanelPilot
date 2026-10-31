import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountSessionProvider } from '@/components/account-session';
import { DesignScreen, REVOKE_AFTER_MS, saveBlob } from '@/components/design-screen';
import { ProjectsPanel } from '@/components/design/projects-panel';
import {
  safeFileName,
  savedName,
  type approveRevision,
  type BoardDesignResponse,
  type designProject,
  type exportDesign,
  type exportRevision,
  type listProjects,
  type openProject,
  type reviseProject,
  type saveProject,
  type SavedProject,
  type SavedRequest,
} from '@/lib/design';
import type * as DesignModule from '@/lib/design';
import type { signIn } from '@/lib/auth';

import { asSignedIn, renderApp } from './helpers';

/**
 * Defects an independent review confirmed: a save overtaken by an open,
 * duplicate and cross-identity exports, approval refusals that leave the
 * readiness stale, unsafe download names, a history row that read an
 * approval as covering, and a clean revision saved again as a copy.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  open: vi.fn<typeof openProject>(),
  save: vi.fn<typeof saveProject>(),
  revise: vi.fn<typeof reviseProject>(),
  approve: vi.fn<typeof approveRevision>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return {
    ...actual,
    listProjects: api.list,
    openProject: api.open,
    saveProject: api.save,
    reviseProject: api.revise,
    approveRevision: api.approve,
  };
});

const SHA = 'a'.repeat(64);

function request(name: string, customer = ''): SavedRequest {
  return {
    info: { name, number: '', customer, consultant: '', contractor: '' },
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
}

function stored(
  id: string,
  name: string,
  revision = 1,
  fields: Record<string, unknown> = {},
  customer = '',
): SavedProject {
  return {
    id,
    name,
    revision,
    validated: true,
    export_status: 'draft',
    permissions: ['approve_revision', 'delete_project', 'edit_company_settings'],
    revisions: Array.from({ length: revision }, (_, index) => ({
      number: index + 1,
      note: '',
      author: 'e',
      approver_account_recorded: false,
      created_at: '2026-10-02T10:00:00Z',
      validated: true,
      export_status: 'draft' as const,
    })),
    request: request(name, customer),
    ...({ findings: [], snapshot_sha256: SHA, rules_version: 'r1', ...fields } as object),
  };
}

function approved(fields: Record<string, unknown> = {}, revision = 1): SavedProject {
  const project = stored('p1', 'Stored tower', revision, {
    approved_snapshot_sha256: SHA,
    ...fields,
  });
  project.revisions = project.revisions.map((entry) => ({
    ...entry,
    approved_by: 'A. Engineer',
    approver_name: 'A. Engineer',
    approver_account_recorded: true,
    approved_at: '2026-10-03T10:00:00Z',
  }));
  return project;
}

/** A promise and the function that settles it, for answering out of order. */
function deferred<T>() {
  let settle: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const DESIGNED = {
  project: {
    info: { name: 'Tower' },
    profile: 'iec-default',
    parts: [],
    boards: [{ id: 'MDB', name: 'MDB', devices: [], cables: [], circuits: [], notes: [] }],
  },
  profile: { key: 'iec-default' },
} as unknown as BoardDesignResponse;

const overview = () => within(screen.getByTestId('view-overview'));
const review = () => within(screen.getByTestId('view-review'));
const panel = () => within(screen.getByTestId('projects-panel'));
const nameField = () => overview().getByLabelText<HTMLInputElement>('Project name');
const customerField = () => overview().getByLabelText<HTMLInputElement>('Customer');

function renderScreen(
  extra: {
    exportRevisionImpl?: typeof exportRevision;
    exportImpl?: typeof exportDesign;
    designImpl?: typeof designProject;
    saveImpl?: (blob: Blob, filename: string) => void;
    signInImpl?: typeof signIn;
    account?: boolean;
  } = {},
) {
  const { account = false, ...impls } = extra;
  const ui: ReactNode = (
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      {...impls}
    />
  );
  renderApp(account ? asSignedIn(ui) : ui);
}

async function openRow(id: string, name: string) {
  const row = await screen.findByTestId(`project-${id}`);
  fireEvent.click(within(row).getByText('Open'));
  await waitFor(() => {
    expect(nameField().value).toBe(name);
  });
}

beforeEach(() => {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: ['approve_revision', 'delete_project', 'edit_company_settings'],
      projects: [
        { id: 'p1', name: 'Stored tower', revisions: 1, updated_at: 'now' },
        { id: 'p2', name: 'Other hall', revisions: 1, updated_at: 'now' },
      ],
    },
  });
  api.open.mockImplementation(({ id }) =>
    Promise.resolve({
      kind: 'saved',
      project: id === 'p2' ? stored('p2', 'Other hall') : stored('p1', 'Stored tower'),
    }),
  );
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
});

describe('a save overtaken by an open', () => {
  it('cannot open another project, change revision or delete while a save is in flight', async () => {
    const saving = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    api.revise.mockReturnValue(saving.promise);
    renderScreen();
    await openRow('p1', 'Stored tower');
    fireEvent.change(customerField(), { target: { value: 'Edited for A' } });
    fireEvent.click(panel().getByRole('button', { name: 'Save as new revision' }));
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });

    // The exact interleaving: Open on B before A's save answers.
    const other = within(screen.getByTestId('project-p2'));
    expect(other.getByText<HTMLButtonElement>('Open').disabled).toBe(true);
    expect(other.getByText<HTMLButtonElement>('Delete').disabled).toBe(true);
    fireEvent.click(other.getByText('Open'));
    await act(async () => {
      saving.settle({
        kind: 'saved',
        project: stored('p1', 'Stored tower', 2, {}, 'Edited for A'),
      });
      await Promise.resolve();
    });

    expect(api.open).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByTestId('project-opened').textContent).toContain('Stored tower');
    });
    // The header and the form are the same project.
    expect(nameField().value).toBe('Stored tower');
    expect(customerField().value).toBe('Edited for A');
    expect(overview().getByLabelText<HTMLSelectElement>('Revision').disabled).toBe(false);
  });

  it('applies only the latest action when an open starts while a save is in flight', async () => {
    // An open granted after the save began (a confirmation answered late)
    // still reaches the server; the save that answers last must not win.
    let granted: (() => void) | null = null;
    const saving = deferred<Awaited<ReturnType<typeof reviseProject>>>();
    const opening = deferred<Awaited<ReturnType<typeof openProject>>>();
    const revise = vi.fn<typeof reviseProject>().mockReturnValue(saving.promise);
    const open = vi.fn<typeof openProject>().mockReturnValue(opening.promise);
    const list = vi.fn<typeof listProjects>().mockResolvedValue({
      kind: 'listed',
      page: {
        permissions: ['approve_revision', 'delete_project', 'edit_company_settings'],
        projects: [{ id: 'p2', name: 'Other hall', revisions: 1, updated_at: 'now' }],
      },
    });
    const onOpened = vi.fn();
    const onSaveState = vi.fn();
    renderApp(
      <ProjectsPanel
        token="tok"
        current={{ name: 'Stored tower', request: request('Stored tower') }}
        opened={{
          id: 'p1',
          name: 'Stored tower',
          revision: 1,
          revisions: [1],
          approval: null,
          approvingAs: null,
          canApprove: true,
        }}
        dirty
        onOpened={onOpened}
        onSaveState={onSaveState}
        beforeOpen={(go) => {
          granted = go;
        }}
        impls={{ list, revise, open }}
      />,
    );
    fireEvent.click(within(await screen.findByTestId('project-p2')).getByText('Open'));
    fireEvent.click(screen.getByRole('button', { name: 'Save as new revision' }));
    await waitFor(() => {
      expect(revise).toHaveBeenCalledTimes(1);
    });
    act(() => {
      granted?.();
    });
    await waitFor(() => {
      expect(open).toHaveBeenCalledTimes(1);
    });

    // B's open answers first, then A's save.
    await act(async () => {
      opening.settle({ kind: 'saved', project: stored('p2', 'Other hall') });
      await Promise.resolve();
    });
    await act(async () => {
      saving.settle({ kind: 'saved', project: stored('p1', 'Stored tower', 2) });
      await Promise.resolve();
    });

    expect(onOpened).toHaveBeenCalledTimes(1);
    expect(onOpened).toHaveBeenCalledWith(stored('p2', 'Other hall'), true);
    // The stale save never rebaselines the page.
    expect(onSaveState).not.toHaveBeenCalledWith('saved');
  });
});

describe('saving an unchanged revision', () => {
  it('is held until the form changes, and allowed for a new project', async () => {
    api.revise.mockResolvedValue({ kind: 'saved', project: stored('p1', 'Stored tower', 2) });
    renderScreen();
    await openRow('p1', 'Stored tower');
    const save = panel().getByRole<HTMLButtonElement>('button', { name: 'Save as new revision' });
    expect(save.disabled).toBe(true);
    expect(panel().getByTestId('save-unchanged').textContent).toBe(
      'Nothing has changed since revision 1: edit the project to save a new revision.',
    );
    fireEvent.click(save);
    expect(api.revise).not.toHaveBeenCalled();

    fireEvent.change(customerField(), { target: { value: 'Changed' } });
    expect(save.disabled).toBe(false);
    expect(panel().queryByTestId('save-unchanged')).toBeNull();
  });

  it.each([
    [
      'an approval that no longer counts (renderer changed)',
      {
        approved_snapshot_sha256: SHA,
        approval_current: false,
        approval_changed: ['renderer_version'],
      },
    ],
    ['a revision saved before Phase 1 (not validated)', { validated: false, findings: [] }],
  ])('allows saving again, unchanged, for %s', async (_case, fields) => {
    const project = stored('p1', 'Stored tower', 1, fields);
    project.revisions = project.revisions.map((entry) => ({
      ...entry,
      approved_by: 'A. Engineer',
      approver_name: 'A. Engineer',
      approver_account_recorded: true,
      approved_at: '2026-10-03T10:00:00Z',
    }));
    api.open.mockResolvedValue({ kind: 'saved', project });
    api.revise.mockResolvedValue({ kind: 'saved', project: stored('p1', 'Stored tower', 2) });
    renderScreen();
    await openRow('p1', 'Stored tower');
    const save = panel().getByRole<HTMLButtonElement>('button', { name: 'Save as new revision' });
    expect(save.disabled).toBe(false);
    expect(panel().queryByTestId('save-unchanged')).toBeNull();
    fireEvent.click(save);
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
  });

  it('lets a new project be saved', () => {
    renderApp(
      <ProjectsPanel
        token="tok"
        current={{ name: 'New', request: request('New') }}
        opened={null}
        onOpened={vi.fn()}
        impls={{
          list: vi
            .fn<typeof listProjects>()
            .mockResolvedValue({ kind: 'listed', page: { projects: [] } }),
        }}
      />,
    );
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Save project' }).disabled).toBe(
      false,
    );
  });
});

describe('the export guard', () => {
  it('sends one revision export for a double click, and the buttons wait for it', async () => {
    const exporting = deferred<Awaited<ReturnType<typeof exportRevision>>>();
    const exportRevisionImpl = vi.fn<typeof exportRevision>().mockReturnValue(exporting.promise);
    const saveImpl = vi.fn();
    renderScreen({ exportRevisionImpl, saveImpl });
    await openRow('p1', 'Stored tower');
    const pdf = review().getByTestId<HTMLButtonElement>('revision-export-pdf');
    fireEvent.click(pdf);
    fireEvent.click(pdf);
    fireEvent.click(review().getByTestId('execution-export'));
    expect(exportRevisionImpl).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(pdf.disabled).toBe(true);
    });
    expect(review().getByTestId<HTMLButtonElement>('execution-export').disabled).toBe(true);
    await act(async () => {
      exporting.settle({ kind: 'exported', blob: new Blob(['x']), filename: 'r1.pdf' });
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(pdf.disabled).toBe(false);
    });
    expect(saveImpl).toHaveBeenCalledTimes(1);
  });

  it('drops a draft export that answers after the trial signed in', async () => {
    api.list.mockResolvedValue({ kind: 'listed', page: { projects: [] } });
    const exporting = deferred<Awaited<ReturnType<typeof exportDesign>>>();
    const exportImpl = vi.fn<typeof exportDesign>().mockReturnValue(exporting.promise);
    const designImpl = vi
      .fn<typeof designProject>()
      .mockResolvedValue({ kind: 'designed', response: DESIGNED });
    const saveImpl = vi.fn();
    const signInImpl = vi.fn<typeof signIn>().mockResolvedValue({
      kind: 'signed-in',
      accessToken: 'header.eyJzdWIiOiJ1c2VyLTEiLCJ0aWQiOiJhY2N0In0.signature',
    });
    renderApp(
      <AccountSessionProvider
        initial={{ status: 'signed-out' }}
        refreshImpl={() => Promise.resolve({ kind: 'expired' })}
      >
        <DesignScreen
          acquireImpl={vi.fn().mockResolvedValue(READY)}
          marketsImpl={vi.fn().mockResolvedValue(null)}
          designImpl={designImpl}
          exportImpl={exportImpl}
          saveImpl={saveImpl}
          signInImpl={signInImpl}
        />
      </AccountSessionProvider>,
    );
    await screen.findByTestId('projects-panel');
    const row = within(screen.getByTestId('design-load-0'));
    fireEvent.change(nameField(), { target: { value: 'Tower' } });
    fireEvent.change(row.getByLabelText('Circuit'), { target: { value: 'Lights' } });
    fireEvent.change(row.getByLabelText(/^Power.*kW/), { target: { value: '1' } });
    fireEvent.click(screen.getByText('Design the board', { selector: 'button' }));
    const draft = within(await review().findByTestId('draft-export'));
    fireEvent.click(draft.getByTestId('design-export-pdf'));
    fireEvent.click(draft.getByTestId('design-export-pdf'));
    expect(exportImpl).toHaveBeenCalledTimes(1);

    fireEvent.click(
      within(screen.getByTestId('design-trial-mode')).getByText('Sign in to save to your account'),
    );
    const form = within(await screen.findByTestId('sign-in-form'));
    fireEvent.change(form.getByLabelText('Email'), { target: { value: 'eng@example.com' } });
    fireEvent.change(form.getByLabelText('Password'), { target: { value: 'correct horse' } });
    fireEvent.click(form.getByRole('button', { name: 'Sign in' }));
    await screen.findByTestId('design-account-mode');

    await act(async () => {
      exporting.settle({ kind: 'exported', blob: new Blob(['x']), filename: 'trial.pdf' });
      await Promise.resolve();
    });
    expect(saveImpl).not.toHaveBeenCalled();
  });
});

describe('approval refusals that change the readiness', () => {
  it.each(['revision_stale', 'revision_not_validated'])(
    'reads the revision again after %s',
    async (code) => {
      api.open
        .mockResolvedValueOnce({ kind: 'saved', project: stored('p1', 'Stored tower') })
        .mockResolvedValue({
          kind: 'saved',
          project: stored(
            'p1',
            'Stored tower',
            1,
            code === 'revision_stale'
              ? { export_status: 'stale' }
              : { validated: false, export_status: 'not_validated' },
          ),
        });
      api.approve.mockResolvedValue({ kind: 'refused', detail: 'no', code });
      renderScreen({ account: true });
      await openRow('p1', 'Stored tower');
      fireEvent.click(overview().getByText('Approve revision 1'));
      await waitFor(() => {
        expect(api.open).toHaveBeenCalledTimes(2);
      });
      expect(api.open.mock.calls[1]?.[0]).toMatchObject({ id: 'p1', revision: 1 });
      await waitFor(() => {
        expect(
          overview().queryByTestId(
            code === 'revision_stale' ? 'approve-held-stale' : 'approve-held-notValidated',
          ),
        ).toBeTruthy();
      });
      // The form is kept as it was.
      expect(nameField().value).toBe('Stored tower');
    },
  );
});

describe('the history row', () => {
  it('does not show a legacy approval as covering the revision', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: approved({ approved_snapshot_sha256: null }),
    });
    renderScreen();
    await openRow('p1', 'Stored tower');
    const badge = screen.getByTestId('revision-approval-1');
    expect(badge.className).toContain('status-warning');
    expect(screen.getByTestId('revision-history').textContent).toContain(
      'not treated as covering revision 1',
    );
  });

  it('shows a covering approval of the revision open as approved', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: approved() });
    renderScreen();
    await openRow('p1', 'Stored tower');
    expect(screen.getByTestId('revision-approval-1').className).toContain('status-success');
  });

  it('does not vouch for an approval of a revision other than the one open', async () => {
    const project = approved({}, 2);
    // Revision 1 approved; revision 2, the one open, not.
    project.revisions = project.revisions.map((entry) =>
      entry.number === 2
        ? { ...entry, approved_at: null, approved_by: null, approver_name: null }
        : entry,
    );
    api.open.mockResolvedValue({ kind: 'saved', project });
    renderScreen();
    await openRow('p1', 'Stored tower');
    expect(screen.getByTestId('revision-approval-1').className).toContain('status-warning');
    expect(screen.getByTestId('revision-history').textContent).toContain(
      'Open revision 1 to check that this approval covers it.',
    );
  });
});

describe('download names', () => {
  it('strips path separators, reserved and control characters from filename*', () => {
    expect(savedName("attachment; filename*=UTF-8''..%2F..%2Fetc%2Fpasswd")).toBe('etc_passwd');
    expect(savedName("attachment; filename*=UTF-8''a%00b%0A%1Fc.pdf")).toBe('a_b__c.pdf');
    expect(savedName("attachment; filename*=UTF-8''C%3A%5Cx%7Cy%3F.pdf")).toBe('C__x_y_.pdf');
    expect(savedName('attachment; filename="..\\..\\boot.ini"')).toBe('boot.ini');
    expect(savedName("attachment; filename*=UTF-8''report...v2.pdf")).toBe('report.v2.pdf');
  });

  it('falls back when nothing safe is left, and caps the length keeping the extension', () => {
    expect(savedName("attachment; filename*=UTF-8''..%2F..%2F")).toBeNull();
    expect(savedName('attachment; filename*=UTF-8\'\'%2F%2F; filename="plain.pdf"')).toBe(
      'plain.pdf',
    );
    const long = safeFileName(`${'x'.repeat(300)}.pdf`);
    expect(long?.length).toBe(120);
    expect(long?.endsWith('.pdf')).toBe(true);
  });

  it('revokes the object URL after the download has started, not at once', () => {
    vi.useFakeTimers();
    const create = vi.fn(() => 'blob:x');
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      expect(revoke).not.toHaveBeenCalled();
    });
    saveBlob(new Blob(['x']), 'a.pdf');
    expect(click).toHaveBeenCalledTimes(1);
    expect(revoke).not.toHaveBeenCalled();
    vi.advanceTimersByTime(REVOKE_AFTER_MS);
    expect(revoke).toHaveBeenCalledWith('blob:x');
    click.mockRestore();
  });
});
