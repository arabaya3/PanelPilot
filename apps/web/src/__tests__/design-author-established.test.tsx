import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderApp } from './helpers';

import { authorEstablishedOf, ProjectsPanel } from '@/components/design/projects-panel';
import {
  resaveRevision,
  type approveRevision,
  type listProjects,
  type Refusal,
  type SavedProject,
} from '@/lib/design';

/** M6b: a revision whose author the server cannot establish is held, and saved again for review. */

const HELD =
  'Approval is held: the server cannot establish who saved revision 2 (it was saved before ' +
  'accounts were recorded with revisions). Save it as a new revision for review; a different ' +
  'team member can then approve it. The new revision records you as its author; it does not ' +
  'show who first entered this design.';

function panel({
  established,
  resave = vi.fn<typeof resaveRevision>(),
  approve = vi.fn<typeof approveRevision>(),
  onOpened = vi.fn<(project: SavedProject, fill: boolean) => void>(),
  onRefused = vi.fn<(refusal: Refusal) => void>(),
  locale = 'en',
  dirty = false,
}: {
  established: boolean | undefined;
  resave?: typeof resaveRevision;
  approve?: typeof approveRevision;
  onOpened?: (project: SavedProject, fill: boolean) => void;
  onRefused?: (refusal: Refusal) => void;
  locale?: 'en' | 'ar' | 'he';
  dirty?: boolean;
}) {
  const list = vi.fn<typeof listProjects>().mockResolvedValue({
    kind: 'listed',
    page: { permissions: ['approve_revision'], projects: [] },
  });
  renderApp(
    <ProjectsPanel
      token="tok"
      current={null}
      dirty={dirty}
      opened={{
        id: 'p1',
        name: 'Clinic',
        revision: 2,
        revisions: [1, 2],
        approval: null,
        // The same name as the recorded author: never used to decide anything.
        approvingAs: 'Omar Nasser',
        canApprove: true,
        isAuthor: false,
        ...(established === undefined ? {} : { authorEstablished: established }),
      }}
      onOpened={onOpened}
      onRefused={onRefused}
      impls={{ list, approve, resave }}
    />,
    { locale },
  );
  return { resave, approve, onOpened, onRefused };
}

describe('a revision whose author is not established', () => {
  it('holds Approve and explains why, from the server flag alone', () => {
    panel({ established: false });
    confirmScope();
    expect(screen.getByRole('button', { name: 'Approve revision 2' })).toHaveProperty(
      'disabled',
      true,
    );
    expect(screen.getByTestId('approve-author-not-established').textContent).toContain(HELD);
    expect(screen.getByTestId('resave-for-review').textContent).toBe(
      'Save as a new revision for review',
    );
  });

  it('offers Approve when established, or when the server does not say', () => {
    for (const established of [true, undefined]) {
      const { unmount } = renderPanel(established);
      confirmScope();
      expect(screen.getByRole('button', { name: 'Approve revision 2' })).toHaveProperty(
        'disabled',
        false,
      );
      expect(screen.queryByTestId('approve-author-not-established')).toBeNull();
      unmount();
    }
  });

  it('saves it again for review and opens the new revision', async () => {
    const project = { id: 'p1', revision: 3 } as unknown as SavedProject;
    const resave = vi.fn<typeof resaveRevision>().mockResolvedValue({ kind: 'saved', project });
    const { onOpened } = panel({ established: false, resave });
    fireEvent.click(screen.getByTestId('resave-for-review'));
    expect((await screen.findByTestId('projects-message')).textContent).toBe(
      'Saved revision 2 again as revision 3, for a different team member to approve.',
    );
    expect(resave).toHaveBeenCalledWith(
      expect.objectContaining({ token: 'tok', id: 'p1', revision: 2 }),
    );
    expect(onOpened).toHaveBeenCalledWith(project, true);
  });

  it('shows a refusal of the stored request and points to the editor; nothing is opened', async () => {
    const resave = vi.fn<typeof resaveRevision>().mockResolvedValue({
      kind: 'refused',
      detail: 'feeder',
      code: 'feeder_server_owned',
      params: { board: 'MDB', load_index: '1' },
    });
    const { onOpened, onRefused } = panel({ established: false, resave });
    fireEvent.click(screen.getByTestId('resave-for-review'));
    const message = (await screen.findByTestId('projects-message')).textContent;
    expect(message).toContain('load row 2');
    expect(message).toContain(
      'Nothing was saved. Correct this in the editor, then save a new revision.',
    );
    expect(onOpened).not.toHaveBeenCalled();
    expect(onRefused).toHaveBeenCalledOnce();
  });

  it.each(['en', 'ar', 'he'] as const)(
    'explains a configured revision a re-save cannot carry, never as "save again" (%s)',
    async (locale) => {
      const resave = vi.fn<typeof resaveRevision>().mockResolvedValue({
        kind: 'refused',
        detail: 'configured',
        code: 'revision_not_validated',
        params: { revision: '2', reason: 'configuration_not_carried' },
      });
      const { onOpened } = panel({ established: false, resave, locale });
      fireEvent.click(screen.getByTestId('resave-for-review'));
      const message = (await screen.findByTestId('projects-message')).textContent;
      if (locale === 'en') {
        expect(message).toContain('Revision 2 holds a connection configuration');
        expect(message).toContain('kept unchanged');
        expect(message).not.toContain('saved before designs were validated');
      } else {
        expect(message).toContain('2');
        expect(message).not.toContain('{revision}');
      }
      expect(onOpened).not.toHaveBeenCalled();
    },
  );

  it('does not save for review while the page has unsaved changes', () => {
    panel({ established: false, dirty: true });
    expect(screen.getByTestId('resave-for-review')).toHaveProperty('disabled', true);
  });

  it.each(['ar', 'he'] as const)('is said in %s', (locale) => {
    panel({ established: false, locale });
    const text = screen.getByTestId('approve-author-not-established').textContent;
    expect(text).not.toContain('Approval is held');
    expect(text).toMatch(/2/);
  });

  it('says the server’s refusal of an approval', async () => {
    const approve = vi.fn<typeof approveRevision>().mockResolvedValue({
      kind: 'refused',
      detail: 'not established',
      code: 'approval_author_not_established',
      params: { revision: '2' },
    });
    // The page did not know (no flag); the server refuses and says why.
    panel({ established: undefined, approve });
    confirmScope();
    fireEvent.click(screen.getByRole('button', { name: 'Approve revision 2' }));
    expect((await screen.findByTestId('projects-message')).textContent).toContain(
      'Revision 2 records no saving account',
    );
  });
});

function renderPanel(established: boolean | undefined) {
  const list = vi.fn<typeof listProjects>().mockResolvedValue({
    kind: 'listed',
    page: { permissions: ['approve_revision'], projects: [] },
  });
  return renderApp(
    <ProjectsPanel
      token="tok"
      current={null}
      opened={{
        id: 'p1',
        name: 'Clinic',
        revision: 2,
        revisions: [1, 2],
        approval: null,
        approvingAs: 'Omar Nasser',
        canApprove: true,
        isAuthor: false,
        ...(established === undefined ? {} : { authorEstablished: established }),
      }}
      onOpened={vi.fn()}
      impls={{ list }}
    />,
    { locale: 'en' },
  );
}

describe('the resave call', () => {
  it('reads the flag from the opened project', () => {
    expect(authorEstablishedOf({ author_established: false } as never)).toBe(false);
    expect(authorEstablishedOf({ author_established: true } as never)).toBe(true);
    expect(authorEstablishedOf({} as never)).toBeUndefined();
  });

  it('posts to the contract’s endpoint with no body of the page', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ code: 'x' }), { status: 409 }));
    await resaveRevision({ token: 'tok', id: 'p 1', revision: 4, fetchImpl });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/design/projects/p%201/revisions/4/resave');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBe('{}');
  });
});

describe('an approval that no longer counts', () => {
  it('says so without calling it current, and offers to save it again for review', async () => {
    const project = { id: 'p1', revision: 3 } as unknown as SavedProject;
    const resave = vi.fn<typeof resaveRevision>().mockResolvedValue({ kind: 'saved', project });
    const list = vi.fn<typeof listProjects>().mockResolvedValue({
      kind: 'listed',
      page: { permissions: ['approve_revision'], projects: [] },
    });
    const onOpened = vi.fn<(project: SavedProject, fill: boolean) => void>();
    renderApp(
      <ProjectsPanel
        token="tok"
        current={null}
        opened={{
          id: 'p1',
          name: 'Clinic',
          revision: 2,
          revisions: [1, 2],
          approval: { by: 'Synthetic Owner A', at: '2026-10-20T10:00:00Z', note: '' },
          approvingAs: 'Omar Nasser',
          canApprove: true,
          isAuthor: false,
          authorEstablished: false,
        }}
        approvalOutdated={['rules_version', 'requirements']}
        onOpened={onOpened}
        impls={{ list, resave }}
      />,
      { locale: 'en' },
    );
    expect(screen.getByTestId('approval-not-current').textContent).toBe(
      'This approval no longer counts for revision 2: it does not match the calculation rules ' +
        'and the requirements record as they are now, or was never tied to them. Do not treat it ' +
        'as current; save it as a new revision for review and have that approved.',
    );
    fireEvent.click(screen.getByTestId('resave-outdated-approval'));
    expect(await screen.findByTestId('projects-message')).toBeTruthy();
    expect(resave).toHaveBeenCalledWith(
      expect.objectContaining({ token: 'tok', id: 'p1', revision: 2 }),
    );
    expect(onOpened).toHaveBeenCalledWith(project, true);
  });
});

/** M6d: tick every approval-scope confirmation on the page (the Approve button waits for it). */
function confirmScope() {
  for (const box of screen.queryAllByTestId<HTMLInputElement>('scope-confirm')) {
    if (!box.checked) fireEvent.click(box);
  }
}
