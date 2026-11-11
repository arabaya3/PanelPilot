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

function stored(): Record<string, unknown> | null {
  const raw = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
  return raw === null ? null : (JSON.parse(raw) as Record<string, unknown>);
}

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
});

describe('unsaved configuration edits across a reload', () => {
  it('are kept in the draft, restored into the editor, still unsaved, and saved as edited', async () => {
    const first = renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    fireEvent.change(designation(), { target: { value: 'X7' } });
    await waitFor(() => {
      expect(stored()).toMatchObject({ project: { id: 'p1', revision: 1 } });
    });
    const kept = parseDraft(stored());
    expect(JSON.stringify(kept?.configuration?.doc)).toContain('X7');
    first.unmount(); // the reload: session storage stays, the page is gone

    renderScreen();
    fireEvent.click(
      within(await screen.findByTestId('draft-offer')).getByText('Restore unsaved work'),
    );
    await waitFor(() => {
      expect(designation().value).toBe('X7');
    });
    // The stored configuration is still the baseline: the edit reads as unsaved.
    expect(screen.getAllByText('Unsaved changes').length).toBeGreaterThan(0);
    // Points and ids the edit did not touch are exactly as stored.
    fireEvent.click(
      within(screen.getByTestId('view-overview')).getByRole('button', {
        name: en.design.projects.saveRevision,
      }),
    );
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalled();
    });
    const sent = api.revise.mock.calls[0]?.[0]?.configuration as typeof STORED;
    expect(sent.strips[0]?.id).toBe('strip-a');
    expect(sent.strips[0]?.points).toEqual([{ id: 'p-a', marking: '1' }]);
    expect(JSON.stringify(sent.strips[0]?.designation)).toContain('X7');
  });

  it('without configuration edits, a restore reopens the stored configuration unchanged', async () => {
    const first = renderScreen();
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
    fireEvent.change(within(screen.getByTestId('view-overview')).getByLabelText('Project name'), {
      target: { value: 'Stored tower B' },
    });
    await waitFor(() => {
      expect(stored()).toMatchObject({ info: { name: 'Stored tower B' } });
    });
    expect(stored()?.configuration).toBeUndefined();
    first.unmount();

    renderScreen();
    fireEvent.click(
      within(await screen.findByTestId('draft-offer')).getByText('Restore unsaved work'),
    );
    await waitFor(() => {
      expect(designation().value).toBe('X1');
    });
  });

  it('a draft configuration without a saved project, or malformed, is not read', () => {
    const base = {
      v: 1,
      session: 's',
      clean: '',
      info: { name: '', number: '', customer: '', consultant: '', contractor: '' },
      boards: [
        {
          name: 'MDB',
          location: '',
          voltage: '400',
          faultLevel: '',
          earthLoop: '',
          fedFrom: '',
          feederLength: '',
          phases: '3',
          earthing: 'TN-S',
          loads: [],
        },
      ],
      profileText: '',
      language: null,
      market: '',
    };
    const doc = { strips: [], components: [], bindings: [], templates: [], patterns: [] };
    expect(
      parseDraft({ ...base, project: null, configuration: { doc, dropUnrecoverable: false } })
        ?.configuration,
    ).toBeUndefined();
    expect(
      parseDraft({
        ...base,
        project: { id: 'p1', revision: 1 },
        configuration: { doc: 'x', dropUnrecoverable: false },
      })?.configuration,
    ).toBeUndefined();
    expect(
      parseDraft({
        ...base,
        project: { id: 'p1', revision: 1 },
        configuration: { doc, dropUnrecoverable: true },
      })?.configuration,
    ).toEqual({ doc, dropUnrecoverable: true });
  });
});
