import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { decided, DESCRIPTION, proposal } from './ai-intake-fixtures';
import { renderApp } from './helpers';

import { DesignScreen } from '@/components/design-screen';
import { DRAFT_STORAGE_KEY } from '@/components/design/draft';
import type {
  AiProposal,
  decideAiItem,
  getAiProposal,
  listProjects,
  requestAiProposal,
  saveProject,
  SavedProject,
} from '@/lib/design';
import type * as DesignModule from '@/lib/design';

/**
 * M6b on the design screen: confirmed items go into the editor's own rows,
 * tagged; a save sends the placements; a failure leaves the work alone; the
 * description is unsaved work kept in the draft.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  save: vi.fn<typeof saveProject>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return { ...actual, listProjects: api.list, saveProject: api.save };
});

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's-ai', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

beforeEach(() => {
  window.sessionStorage.clear();
  api.list.mockResolvedValue({
    kind: 'listed',
    page: { permissions: [], projects: [] },
  });
});

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/');
});

/** A decide stub that records each decision as the server would. */
function serverDecides() {
  let current: AiProposal = proposal();
  const decide = vi.fn<typeof decideAiItem>(({ item, decision, value }) => {
    current = decided(current, item, decision, value);
    return Promise.resolve({ kind: 'proposed', proposal: current });
  });
  return decide;
}

function renderScreen(
  impls: {
    request?: typeof requestAiProposal;
    decide?: typeof decideAiItem;
    get?: typeof getAiProposal;
  } = {},
) {
  return renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      {...(impls.request ? { requestAiProposalImpl: impls.request } : {})}
      {...(impls.decide ? { decideAiItemImpl: impls.decide } : {})}
      {...(impls.get ? { getAiProposalImpl: impls.get } : {})}
    />,
  );
}

function viewTab(label: string): HTMLElement {
  const tab = screen
    .getByText(label, { selector: '[role="tab"], [role="tab"] *' })
    .closest('[role="tab"]');
  if (!(tab instanceof HTMLElement)) throw new Error(`no tab ${label}`);
  return tab;
}

const requirements = () => within(screen.getByTestId('view-requirements'));

/** Acknowledge that the description goes to the AI provider. */
function agreeToSend() {
  const box = requirements().getByTestId<HTMLInputElement>('ai-consent');
  if (!box.checked) fireEvent.click(box);
}
const loadsPanel = () => within(screen.getByTestId('requirements-loads'));
const descriptions = () => loadsPanel().getAllByLabelText<HTMLInputElement>('Circuit');
const boardNames = () =>
  within(screen.getByTestId('requirements-boards'))
    .getAllByLabelText<HTMLInputElement>('Board name')
    .map((input) => input.value);

async function openWithProposal(decide = serverDecides()) {
  const request = vi
    .fn<typeof requestAiProposal>()
    .mockResolvedValue({ kind: 'proposed', proposal: proposal() });
  renderScreen({ request, decide });
  await screen.findByTestId('projects-panel');
  fireEvent.click(viewTab('Requirements'));
  fireEvent.change(requirements().getByTestId('ai-description'), {
    target: { value: DESCRIPTION },
  });
  agreeToSend();
  fireEvent.click(requirements().getByTestId('ai-request'));
  await requirements().findByTestId('ai-review');
  return { request, decide };
}

async function accept(item: string) {
  fireEvent.click(requirements().getByTestId(`ai-accept-${item}`));
  await waitFor(() => {
    expect(requirements().getByTestId(`ai-status-${item}`).textContent).toBe('Accepted');
  });
}

describe('AI proposals on the design screen', () => {
  it('inserts accepted items into the editor rows, and nothing else', async () => {
    await openWithProposal();
    expect(boardNames()).toEqual(['DB1']);
    await accept('i-board');
    expect(boardNames()).toEqual(['DB1', 'MDB']);
    await accept('i-pump');
    expect(descriptions().map((input) => input.value)).toEqual(['', 'Pump P1']);
    await accept('i-start');
    expect(requirements().getByTestId('ai-placed-i-start')).toBeTruthy();
    const startStop = requirements().getAllByTestId(/^control-start_stop-/);
    expect((startStop[1] as HTMLInputElement).checked).toBe(true);
    // A rejected item is never inserted; a question or unsupported text has nowhere to go.
    fireEvent.click(requirements().getByTestId('ai-reject-i-light'));
    await waitFor(() => {
      expect(requirements().getByTestId('ai-status-i-light').textContent).toBe('Rejected');
    });
    expect(descriptions()).toHaveLength(2);
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
  });

  it('saves the placements, keeping a tag through an edit of the row', async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: savedProject() });
    await openWithProposal();
    await accept('i-board');
    await accept('i-pump');
    // The engineer completes the form and edits the placed row.
    fireEvent.change(descriptions()[0] as HTMLInputElement, { target: { value: 'Sockets' } });
    const powers = loadsPanel().getAllByLabelText<HTMLInputElement>(/^Power/);
    fireEvent.change(powers[0] as HTMLInputElement, { target: { value: '2' } });
    fireEvent.change(descriptions()[1] as HTMLInputElement, {
      target: { value: 'Pump P1 (duty)' },
    });
    expect(requirements().getByTestId('ai-placed-i-pump')).toBeTruthy();
    fireEvent.click(viewTab('Overview'));
    fireEvent.change(within(screen.getByTestId('view-overview')).getByLabelText('Project name'), {
      target: { value: 'AI tower' },
    });
    fireEvent.click(within(screen.getByTestId('projects-panel')).getByText('Save project'));
    await waitFor(() => {
      expect(api.save).toHaveBeenCalledTimes(1);
    });
    const sent = api.save.mock.calls[0]?.[0];
    expect(sent?.requirementsInput).toEqual({
      intake: 'ai',
      control: [],
      ai: {
        proposal_id: 'prop-1',
        placements: [
          { item_id: 'i-board', board_index: 1 },
          { item_id: 'i-pump', board_index: 1, load_index: 0 },
        ],
      },
    });
    // The request holds the row as edited, and only what was given.
    const mdb = sent?.request.boards[1];
    expect(mdb?.loads[0]).toMatchObject({
      description: 'Pump P1 (duty)',
      power_kw: '7.5',
      phases: 3,
      power_factor: null,
      length_m: null,
    });
    expect(JSON.stringify(sent?.request)).not.toContain('aiItem');
    // The placed board has no supply voltage the description did not give.
    expect(mdb?.supply?.voltage_v).toBe('');
  });

  it('drops the placement of a removed row', async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: savedProject() });
    await openWithProposal();
    await accept('i-board');
    await accept('i-pump');
    // Remove the placed pump row: the MDB board's only row cannot be removed, so add one first.
    const mdbSection = loadsPanel().getByText('Loads on MDB').closest('section') as HTMLElement;
    fireEvent.click(within(mdbSection).getByText('Add circuit'));
    const removes = within(mdbSection).getAllByRole('button', { name: 'Remove' });
    fireEvent.click(removes[0] as HTMLElement);
    expect(requirements().queryByTestId('ai-placed-i-pump')).toBeNull();
    // Decidable again once its row is gone.
    expect(requirements().getByTestId<HTMLButtonElement>('ai-reject-i-pump').disabled).toBe(false);
  });

  it('a failed request changes neither the editor, the draft nor the description', async () => {
    const request = vi
      .fn<typeof requestAiProposal>()
      .mockResolvedValue({ kind: 'refused', detail: '', code: 'ai_unavailable' });
    renderScreen({ request });
    await screen.findByTestId('projects-panel');
    fireEvent.click(viewTab('Requirements'));
    fireEvent.change(descriptions()[0] as HTMLInputElement, { target: { value: 'Typed load' } });
    fireEvent.change(requirements().getByTestId('ai-description'), {
      target: { value: DESCRIPTION },
    });
    await waitFor(() => {
      expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).not.toBeNull();
    });
    const before = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
    agreeToSend();
    fireEvent.click(requirements().getByTestId('ai-request'));
    await requirements().findByTestId('ai-error');
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).toBe(before);
    expect(descriptions().map((input) => input.value)).toEqual(['Typed load']);
    expect(boardNames()).toEqual(['DB1']);
    expect(requirements().getByTestId<HTMLTextAreaElement>('ai-description').value).toBe(
      DESCRIPTION,
    );
  });

  it('keeps the typed description in the unsaved-work draft', async () => {
    renderScreen();
    await screen.findByTestId('projects-panel');
    fireEvent.click(viewTab('Requirements'));
    expect(screen.queryByText('Unsaved changes')).toBeNull();
    fireEvent.change(requirements().getByTestId('ai-description'), {
      target: { value: 'Only a description' },
    });
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
    await waitFor(() => {
      const raw = window.sessionStorage.getItem(DRAFT_STORAGE_KEY) ?? 'null';
      expect(JSON.parse(raw)).toMatchObject({
        ai: { description: 'Only a description', proposalId: null },
      });
    });
  });

  it('takes the tags off and closes the proposal once a save used it', async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: savedProject() });
    await openWithProposal();
    await accept('i-board');
    await accept('i-pump');
    fireEvent.change(descriptions()[0] as HTMLInputElement, { target: { value: 'Sockets' } });
    const powers = loadsPanel().getAllByLabelText<HTMLInputElement>(/^Power/);
    fireEvent.change(powers[0] as HTMLInputElement, { target: { value: '2' } });
    fireEvent.click(viewTab('Overview'));
    fireEvent.change(within(screen.getByTestId('view-overview')).getByLabelText('Project name'), {
      target: { value: 'AI tower' },
    });
    fireEvent.click(within(screen.getByTestId('projects-panel')).getByText('Save project'));
    await waitFor(() => {
      expect(api.save).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(viewTab('Requirements'));
    await waitFor(() => {
      expect(requirements().queryByTestId('ai-review')).toBeNull();
    });
    // The rows stay, as entered; the description stays too.
    expect(descriptions().map((input) => input.value)).toEqual(['Sockets', 'Pump P1']);
    expect(requirements().getByTestId<HTMLTextAreaElement>('ai-description').value).toBe(
      DESCRIPTION,
    );
  });

  it('reads the proposal of a restored draft again from the server', async () => {
    const draft = {
      v: 1,
      session: 's-ai',
      project: null,
      clean: '',
      info: { name: 'Kept', number: '', customer: '', consultant: '', contractor: '' },
      boards: [
        {
          name: 'MDB',
          location: '',
          voltage: '',
          phases: '3',
          faultLevel: '',
          earthing: 'TN-S',
          earthLoop: '',
          fedFrom: '',
          feederLength: '',
          aiItem: 'i-board',
          loads: [],
        },
      ],
      profileText: '',
      language: null,
      market: '',
      ai: { description: DESCRIPTION, proposalId: 'prop-1', controlTags: [] },
    };
    window.sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft));
    const get = vi
      .fn<typeof getAiProposal>()
      .mockResolvedValue({ kind: 'proposed', proposal: decided(proposal(), 'i-board', 'accept') });
    renderScreen({ get });
    fireEvent.click(
      within(await screen.findByTestId('draft-offer')).getByText('Restore unsaved work'),
    );
    await waitFor(() => {
      expect(get).toHaveBeenCalledWith(expect.objectContaining({ id: 'prop-1' }));
    });
    fireEvent.click(viewTab('Requirements'));
    expect(requirements().getByTestId<HTMLTextAreaElement>('ai-description').value).toBe(
      DESCRIPTION,
    );
    await requirements().findByTestId('ai-review');
    expect(requirements().getByTestId('ai-placed-i-board')).toBeTruthy();
    expect(requirements().getByTestId('ai-status-i-board').textContent).toBe('Accepted');
  });
});

function savedProject(): SavedProject {
  return {
    id: 'p1',
    name: 'AI tower',
    revision: 1,
    validated: true,
    findings: [],
    export_status: 'not_validated',
    author_unknown: false,
    author_established: true,
    is_author: true,
    permissions: [],
    revisions: [
      {
        number: 1,
        note: '',
        author: 'e',
        approver_account_recorded: false,
        author_unknown: false,
        author_established: true,
        created_at: '2026-11-04T10:00:00Z',
        validated: true,
        export_status: 'not_validated' as const,
      },
    ],
    request: {
      info: { name: 'AI tower', number: '', customer: '', consultant: '', contractor: '' },
      boards: [],
      profile: null,
    },
  };
}
