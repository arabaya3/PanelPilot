import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import {
  DRAFT_STORAGE_KEY,
  draftBoards,
  formBoards,
  parseDraft,
  readDraft,
} from '@/components/design/draft';
import { blankBoard } from '@/components/design/schedule';
import type {
  listProjects,
  openProject,
  saveProject,
  SavedProject,
  SavedRequest,
} from '@/lib/design';

import type * as DesignModule from '@/lib/design';

import { renderApp } from './helpers';

/**
 * Unsaved work kept as a draft: what is stored, for which session, and how
 * it is offered back after the page was left in a way the guard cannot hold.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  save: vi.fn<typeof saveProject>(),
  open: vi.fn<typeof openProject>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return {
    ...actual,
    listProjects: api.list,
    saveProject: api.save,
    openProject: api.open,
  };
});

function ready(sessionId: string) {
  return {
    kind: 'ready' as const,
    accessToken: 'secret-token',
    trial: { sessionId, claimSecret: 'claim-secret' },
    questionsRemaining: 5,
    conversationId: null,
  };
}

function renderScreen(sessionId = 's1') {
  return renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(ready(sessionId))}
      marketsImpl={vi.fn().mockResolvedValue(null)}
    />,
  );
}

const overview = () => within(screen.getByTestId('view-overview'));
const projects = () => within(screen.getByTestId('projects-panel'));
const nameField = () => overview().getByLabelText<HTMLInputElement>('Project name');
const loadRow = () => within(screen.getByTestId('design-load-0'));

function type(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

function stored(): Record<string, unknown> | null {
  const raw = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
  return raw === null ? null : (JSON.parse(raw) as Record<string, unknown>);
}

/** Type into a fresh page, then leave it without the guard (unmount). */
async function leaveDirty(sessionId = 's1') {
  const { unmount } = renderScreen(sessionId);
  await screen.findByTestId('projects-panel');
  type(nameField(), 'Kept tower');
  type(loadRow().getByLabelText('Circuit'), 'Lights');
  await waitFor(() => {
    expect(stored()).toMatchObject({ session: sessionId, info: { name: 'Kept tower' } });
  });
  unmount();
}

const SAVED_REQUEST = {
  info: { name: 'Stored tower', number: '', customer: '', consultant: '', contractor: '' },
  boards: [
    {
      name: 'MDB',
      location: null,
      fed_from: null,
      feeder_length_m: null,
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [],
    },
  ],
  profile: null,
} as unknown as SavedRequest;

function saved(revision = 1): SavedProject {
  return {
    id: 'p1',
    name: 'Stored tower',
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
    request: SAVED_REQUEST,
  };
}

beforeEach(() => {
  api.list.mockResolvedValue({ kind: 'listed', page: { projects: [] } });
});

afterEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '/');
});

describe('the stored draft', () => {
  it('keeps what was typed, for the session it was typed in, and no credential', async () => {
    await leaveDirty('s1');
    const raw = window.sessionStorage.getItem(DRAFT_STORAGE_KEY) ?? '';
    expect(raw).not.toContain('secret-token');
    expect(raw).not.toContain('claim-secret');
    expect(readDraft('s1')?.info.name).toBe('Kept tower');
  });

  it('is never offered to another session, and is removed', async () => {
    await leaveDirty('s1');
    renderScreen('s2');
    await screen.findByTestId('projects-panel');
    await waitFor(() => {
      expect(stored()).toBeNull();
    });
    expect(screen.queryByTestId('draft-offer')).toBeNull();
    expect(nameField().value).toBe('');
  });

  it('drops malformed or older data instead of offering it', () => {
    const valid = {
      v: 1,
      session: 's1',
      project: null,
      clean: '',
      info: { name: 'A', number: '', customer: '', consultant: '', contractor: '' },
      boards: draftBoards([blankBoard(0, 1, 'DB1')]),
      profileText: '',
      language: null,
      market: '',
    };
    expect(parseDraft(valid)).not.toBeNull();
    expect(parseDraft({ ...valid, v: 0 })).toBeNull();
    expect(parseDraft({ ...valid, boards: [] })).toBeNull();
    expect(parseDraft({ ...valid, language: 'fr' })).toBeNull();
    expect(parseDraft({ ...valid, project: { id: 'p1', revision: 0 } })).toBeNull();
    expect(parseDraft({ ...valid, info: { name: 1 } })).toBeNull();
    const badLoad = { ...valid.boards[0], loads: [{ ...valid.boards[0]?.loads[0], load: 'x' }] };
    expect(parseDraft({ ...valid, boards: [badLoad] })).toBeNull();
    expect(parseDraft('nonsense')).toBeNull();

    window.sessionStorage.setItem(DRAFT_STORAGE_KEY, '{not json');
    expect(readDraft('s1')).toBeNull();
    expect(stored()).toBeNull();
  });

  it('numbers restored boards and rows afresh', () => {
    const boards = draftBoards([blankBoard(4, 5, 'DB1'), blankBoard(9, 10, 'DB2')]);
    const keyed = formBoards(boards, 100);
    const keys = keyed.boards.flatMap((board) => [board.key, ...board.loads.map((l) => l.key)]);
    expect(new Set(keys).size).toBe(keys.length);
    expect(Math.min(...keys)).toBe(100);
    expect(keyed.used).toBe(keys.length);
  });
});

describe('while the trial cannot be resumed', () => {
  it('still keeps the work, for the trial this browser holds', async () => {
    window.localStorage.setItem(
      'panelpilot.trial',
      JSON.stringify({ sessionId: 'held', claimSecret: 'claim-secret' }),
    );
    try {
      renderApp(
        <DesignScreen
          acquireImpl={vi.fn().mockResolvedValue({ kind: 'rate-limited' })}
          marketsImpl={vi.fn().mockResolvedValue(null)}
        />,
      );
      // Said as what it is, not as the service being down.
      await screen.findByText(/Too many requests from this network/);
      type(nameField(), 'Typed offline');
      await waitFor(() => {
        expect(stored()).toMatchObject({ session: 'held', info: { name: 'Typed offline' } });
      });
      expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).not.toContain('claim-secret');
    } finally {
      window.localStorage.removeItem('panelpilot.trial');
    }
  });
});

describe('offering the draft back', () => {
  it('restores the work as unsaved', async () => {
    await leaveDirty();
    renderScreen();
    const offer = await screen.findByTestId('draft-offer');
    // Nothing is replaced until the engineer asks.
    expect(nameField().value).toBe('');
    fireEvent.click(within(offer).getByText('Restore unsaved work'));
    await waitFor(() => {
      expect(nameField().value).toBe('Kept tower');
    });
    expect(loadRow().getByLabelText<HTMLInputElement>('Circuit').value).toBe('Lights');
    expect(screen.queryByTestId('draft-offer')).toBeNull();
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
    expect(stored()).toMatchObject({ info: { name: 'Kept tower' } });
  });

  it('forgets the draft when the engineer discards it', async () => {
    await leaveDirty();
    renderScreen();
    const offer = await screen.findByTestId('draft-offer');
    fireEvent.click(within(offer).getByText('Discard it'));
    expect(screen.queryByTestId('draft-offer')).toBeNull();
    expect(stored()).toBeNull();
    expect(nameField().value).toBe('');
  });

  it('reopens the saved project the work belongs to', async () => {
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({
        v: 1,
        session: 's1',
        project: { id: 'p1', revision: 1 },
        clean: 'stored',
        info: { name: 'Edited tower', number: '', customer: '', consultant: '', contractor: '' },
        boards: draftBoards([blankBoard(0, 1, 'MDB')]),
        profileText: '',
        language: null,
        market: '',
      }),
    );
    api.open.mockResolvedValue({ kind: 'saved', project: saved() });
    renderScreen();
    fireEvent.click(
      within(await screen.findByTestId('draft-offer')).getByText('Restore unsaved work'),
    );
    await waitFor(() => {
      expect(nameField().value).toBe('Edited tower');
    });
    expect(api.open).toHaveBeenCalledWith(expect.objectContaining({ id: 'p1', revision: 1 }));
    expect(screen.getByTestId('project-opened').textContent).toContain('Stored tower');
    expect(screen.queryByTestId('draft-project-gone')).toBeNull();
  });

  it('says so when the project it belonged to cannot be opened', async () => {
    window.sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({
        v: 1,
        session: 's1',
        project: { id: 'gone', revision: 2 },
        clean: 'stored',
        info: { name: 'Orphan', number: '', customer: '', consultant: '', contractor: '' },
        boards: draftBoards([blankBoard(0, 1, 'MDB')]),
        profileText: '',
        language: null,
        market: '',
      }),
    );
    api.open.mockResolvedValue({ kind: 'failed' });
    renderScreen();
    fireEvent.click(
      within(await screen.findByTestId('draft-offer')).getByText('Restore unsaved work'),
    );
    expect(await screen.findByTestId('draft-project-gone')).toBeTruthy();
    expect(nameField().value).toBe('Orphan');
    expect(screen.queryByTestId('project-opened')).toBeNull();
  });
});

describe('clearing the draft', () => {
  it('clears it once the work is saved', async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: saved() });
    renderScreen();
    await screen.findByTestId('projects-panel');
    type(nameField(), 'Tower');
    type(loadRow().getByLabelText('Circuit'), 'Lights');
    type(loadRow().getByLabelText(/^Power.*kW/), '1');
    await waitFor(() => {
      expect(stored()).not.toBeNull();
    });
    fireEvent.click(projects().getByText('Save project'));
    await waitFor(() => {
      expect(stored()).toBeNull();
    });
  });

  it('clears it when the edits are undone', async () => {
    renderScreen();
    await screen.findByTestId('projects-panel');
    type(nameField(), 'Tower');
    await waitFor(() => {
      expect(stored()).not.toBeNull();
    });
    type(nameField(), '');
    await waitFor(() => {
      expect(stored()).toBeNull();
    });
  });

  it('clears it when the engineer discards the changes to leave', async () => {
    const leaveImpl = vi.fn();
    renderApp(
      <DesignScreen
        acquireImpl={vi.fn().mockResolvedValue(ready('s1'))}
        marketsImpl={vi.fn().mockResolvedValue(null)}
        leaveImpl={leaveImpl}
      />,
    );
    await screen.findByTestId('projects-panel');
    type(nameField(), 'Tower');
    await waitFor(() => {
      expect(stored()).not.toBeNull();
    });
    const link = screen.getByText('Calculate').closest('a');
    if (link === null) throw new Error('no rail link');
    fireEvent.click(link);
    fireEvent.click(within(screen.getByTestId('unsaved-dialog')).getByText('Discard changes'));
    expect(leaveImpl).toHaveBeenCalled();
    // A render after the choice must not write it back.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(stored()).toBeNull();
  });
});
