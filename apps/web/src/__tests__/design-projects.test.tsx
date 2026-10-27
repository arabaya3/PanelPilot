import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { approvalOf, ProjectsPanel } from '@/components/design/projects-panel';
import { fromRequest, profileFrom } from '@/components/design/schedule';
import {
  type approveRevision,
  deleteProject,
  listProjects,
  openProject,
  type reviseProject,
  type saveProject,
  type ProjectDesignRequest,
  type SavedProject,
  type SavedRequest,
} from '@/lib/design';

import { renderApp } from './helpers';

/** Saved projects: save, revise, open, delete, and the form they come back as. */

const REQUEST = {
  info: { name: 'Tower' },
  boards: [{ name: 'MDB', loads: [{ description: 'Lights', load: 'lighting', power_kw: '1' }] }],
} as unknown as ProjectDesignRequest;

const SAVED_REQUEST = {
  info: { name: 'Tower', number: 'J-1', customer: '', consultant: '', contractor: '' },
  boards: [
    {
      name: 'MDB',
      location: null,
      fed_from: null,
      feeder_length_m: null,
      supply: { voltage_v: '400', phases: 3, fault_level_ka: '25' },
      loads: [
        {
          description: 'Pump',
          load: 'motor',
          power_kw: '7.5',
          phases: 3,
          power_factor: null,
          controlled: false,
          starter: 'dol',
          length_m: '40',
        },
      ],
    },
    {
      name: 'DB1',
      location: 'Hall',
      fed_from: 'MDB',
      feeder_length_m: '60',
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [
        {
          description: 'Sockets',
          load: 'socket',
          power_kw: '2',
          phases: 1,
          power_factor: null,
          controlled: false,
          starter: null,
          length_m: null,
        },
      ],
    },
  ],
  profile: { key: 'iec-default', language: 'ar' },
} as unknown as SavedRequest;

function project(revision: number): SavedProject {
  return {
    id: 'p1',
    name: 'Tower',
    revision,
    validated: false,
    export_status: 'not_validated',
    revisions: Array.from({ length: revision }, (_, index) => ({
      number: index + 1,
      note: '',
      author: 'e',
      approver_account_recorded: false,
      created_at: '2026-10-02T10:00:00Z',
      validated: false,
      export_status: 'not_validated' as const,
    })),
    request: SAVED_REQUEST,
  };
}

function approvedProject(): SavedProject {
  const saved = project(1);
  const [first] = saved.revisions;
  return first
    ? {
        ...saved,
        revisions: [
          {
            ...first,
            approved_by: 'Rana Haddad',
            approver_name: 'Rana Haddad',
            approver_account_recorded: true,
            approval_note: 'Site check',
            approved_at: '2026-10-02T12:00:00Z',
          },
        ],
      }
    : saved;
}

describe('a saved project back as the form', () => {
  it('keeps every board, supply, feeder and row, with fresh keys', () => {
    const { info, boards, used } = fromRequest(SAVED_REQUEST, 10);
    expect(info.number).toBe('J-1');
    expect(boards.map((board) => [board.key, board.name, board.fedFrom])).toEqual([
      [10, 'MDB', ''],
      [12, 'DB1', 'MDB'],
    ]);
    expect(boards[0]).toMatchObject({ faultLevel: '25', phases: '3' });
    expect(boards[1]).toMatchObject({ feederLength: '60', location: 'Hall' });
    expect(boards[0]?.loads[0]).toMatchObject({ key: 11, starter: 'dol', length: '40' });
    expect(used).toBe(4);
  });

  it('puts the language in its field and drops a bare default profile', () => {
    expect(profileFrom({ key: 'iec-default', language: 'ar' })).toEqual({
      text: '',
      language: 'ar',
    });
    expect(profileFrom({ key: 'acme', max_circuits_per_rcd: 8 }).text).toContain('acme');
    expect(profileFrom(null)).toEqual({ text: '', language: null });
  });
});

describe('the projects panel', () => {
  function render(opened: Parameters<typeof ProjectsPanel>[0]['opened'] = null, dirty = false) {
    const list = vi.fn<typeof listProjects>().mockResolvedValue({
      kind: 'listed',
      page: { projects: [{ id: 'p1', name: 'Tower', revisions: 2, updated_at: 'now' }] },
    });
    const save = vi
      .fn<typeof saveProject>()
      .mockResolvedValue({ kind: 'saved', project: project(1) });
    const revise = vi
      .fn<typeof reviseProject>()
      .mockResolvedValue({ kind: 'saved', project: project(2) });
    const open = vi
      .fn<typeof openProject>()
      .mockResolvedValue({ kind: 'saved', project: project(2) });
    const remove = vi.fn<typeof deleteProject>().mockResolvedValue({ kind: 'deleted' });
    const approve = vi.fn<typeof approveRevision>().mockResolvedValue({
      kind: 'saved',
      project: approvedProject(),
    });
    const onOpened = vi.fn();
    renderApp(
      <ProjectsPanel
        token="tok"
        current={{ name: 'Tower', request: REQUEST }}
        opened={opened}
        dirty={dirty}
        onOpened={onOpened}
        impls={{ list, save, revise, open, remove, approve }}
      />,
    );
    return { list, save, revise, open, remove, approve, onOpened };
  }

  it('saves a new project, then lists it', async () => {
    const { save, onOpened } = render();
    expect(await screen.findByText('Tower')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Revision note'), { target: { value: 'issue A' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save project' }));
    await waitFor(() => {
      expect(onOpened).toHaveBeenCalledWith(project(1), false);
    });
    expect(save.mock.calls[0]?.[0]).toMatchObject({ name: 'Tower', note: 'issue A' });
    expect(screen.getByTestId('projects-message').textContent).toBe('Saved as revision 1.');
  });

  it('lists once, not on every render', async () => {
    // Typing re-renders the panel. The list depends only on who is signed in,
    // so a keystroke must not refetch it.
    const { list } = render();
    expect(await screen.findByText('Tower')).toBeTruthy();
    for (const value of ['i', 'is', 'iss', 'issue']) {
      fireEvent.change(screen.getByLabelText('Revision note'), { target: { value } });
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('says a failed list once instead of retrying it in a loop', async () => {
    // The loop this guards against: the failure set a message, the message
    // re-rendered the panel, a new `say` closure gave `refresh` a new
    // identity, and the effect fetched again -- until the per-address design
    // limit refused every request on the page, the design itself included.
    const list = vi.fn<typeof listProjects>().mockResolvedValue({
      kind: 'refused',
      detail: 'too many design requests from this network',
      code: 'rate_limited',
    });
    renderApp(
      <ProjectsPanel
        token="tok"
        current={{ name: 'Tower', request: REQUEST }}
        opened={null}
        onOpened={vi.fn()}
        impls={{ list }}
      />,
    );
    expect((await screen.findByTestId('projects-message')).textContent).toBe(
      'Too many design requests from this network; wait a few minutes.',
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('saves the open project as a new revision, and opens an earlier one', async () => {
    // Changed since revision 2: an unchanged revision is not saved again.
    const { revise, open, onOpened } = render(
      {
        id: 'p1',
        name: 'Tower',
        revision: 2,
        revisions: [1, 2],
        approval: null,
        approvingAs: 'Rana Haddad',
      },
      true,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save as new revision' }));
    await waitFor(() => {
      expect(revise).toHaveBeenCalled();
    });
    expect(revise.mock.calls[0]?.[0]).toMatchObject({ id: 'p1' });
    fireEvent.change(screen.getByLabelText('Revision'), { target: { value: '1' } });
    await waitFor(() => {
      expect(open.mock.calls[0]?.[0]).toMatchObject({ id: 'p1', revision: 1 });
    });
    expect(onOpened).toHaveBeenLastCalledWith(project(2), true);
  });

  it('approves as the signed-in account, with an optional note, asking no name', async () => {
    const { approve, onOpened } = render({
      id: 'p1',
      name: 'Tower',
      revision: 1,
      revisions: [1],
      approval: null,
      approvingAs: 'Rana Haddad',
    });
    // No field asks who approves: the server names the account.
    expect(screen.queryByLabelText('Approving engineer')).toBeNull();
    expect(screen.getByTestId('approving-as').textContent).toBe(
      'You are approving as Rana Haddad.',
    );
    const button = screen.getByRole('button', { name: 'Approve revision 1' });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.change(screen.getByLabelText('Approval note (optional)'), {
      target: { value: ' Site check ' },
    });
    fireEvent.click(button);
    await waitFor(() => {
      expect(onOpened).toHaveBeenCalledWith(approvedProject(), false);
    });
    const sent = approve.mock.calls[0]?.[0];
    expect(sent).toMatchObject({ id: 'p1', revision: 1, note: 'Site check' });
    expect(sent).not.toHaveProperty('approver');
    // The approver shown is the server's, never the note.
    expect(approvalOf(approvedProject())).toEqual({
      by: 'Rana Haddad',
      at: '2026-10-02T12:00:00Z',
      note: 'Site check',
    });
    expect(approvalOf(project(1))).toBeNull();
  });

  it('shows an approved revision as approved, with nothing to sign', () => {
    render({
      id: 'p1',
      name: 'Tower',
      revision: 1,
      revisions: [1],
      approval: { by: 'Rana Haddad', at: '2026-10-02T12:00:00Z', note: 'A. Rabaya' },
      approvingAs: 'Rana Haddad',
    });
    expect(screen.getByTestId('project-approval').textContent).toBe(
      'Revision 1 approved by Rana Haddad on 2026-10-02.',
    );
    // The note is labelled as a note, apart from who approved.
    expect(screen.getByTestId('project-approval-note').textContent).toBe(
      'Approval note: A. Rabaya',
    );
    expect(screen.queryByLabelText('Approval note (optional)')).toBeNull();
  });

  it('names a team member when the approving account has no display name', () => {
    render({
      id: 'p1',
      name: 'Tower',
      revision: 1,
      revisions: [1],
      approval: { by: null, at: '2026-10-02T12:00:00Z', note: null },
      approvingAs: null,
    });
    expect(screen.getByTestId('project-approval').textContent).toBe(
      'Revision 1 approved by a team member on 2026-10-02.',
    );
    expect(screen.queryByTestId('project-approval-note')).toBeNull();
  });

  it('says a team member is approving when the account has no display name', () => {
    render({
      id: 'p1',
      name: 'Tower',
      revision: 1,
      revisions: [1],
      approval: null,
      approvingAs: null,
    });
    expect(screen.getByTestId('approving-as').textContent).toBe(
      'You are approving as a team member.',
    );
  });

  it('asks before deleting', async () => {
    const { remove } = render();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Delete for good?' }));
    await waitFor(() => {
      expect(remove).toHaveBeenCalledWith(expect.objectContaining({ id: 'p1' }));
    });
  });
});

describe('the project calls', () => {
  it('lists with GET, deletes with DELETE, and reads a 404 as a refusal', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ projects: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'no such project', code: 'project_not_found' }), {
          status: 404,
        }),
      );
    expect((await listProjects({ token: 't', fetchImpl, cursor: 'a b' })).kind).toBe('listed');
    expect(fetchImpl.mock.calls[0]?.[0]).toBe('/api/v1/design/projects?cursor=a%20b');
    expect(fetchImpl.mock.calls[0]?.[1]).toMatchObject({ method: 'GET' });
    expect(await deleteProject({ token: 't', fetchImpl, id: 'p1' })).toEqual({ kind: 'deleted' });
    expect(fetchImpl.mock.calls[1]?.[1]).toMatchObject({ method: 'DELETE' });
    const missing = await openProject({ token: 't', fetchImpl, id: 'p1', revision: 3 });
    expect(missing).toMatchObject({ kind: 'refused', code: 'project_not_found' });
    expect(fetchImpl.mock.calls[2]?.[0]).toBe('/api/v1/design/projects/p1?revision=3');
  });
});
