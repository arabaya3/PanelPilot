import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { decided, DESCRIPTION, proposal } from './ai-intake-fixtures';
import { renderApp } from './helpers';

import { AiIntake } from '@/components/design/ai-intake';
import type { PlaceProblem } from '@/components/design/ai-intake-model';
import { RequirementsView } from '@/components/design/requirements-view';
import { blankBoard } from '@/components/design/schedule';
import type {
  AiItem,
  AiProposal,
  AiProposalOutcome,
  decideAiItem,
  deleteAiProposal,
  requestAiProposal,
  RequirementsRecord,
} from '@/lib/design';

/** M6b: the "Describe the project" step and the proposals review, on their own. */

type Props = {
  initial?: AiProposal | null;
  request?: ReturnType<typeof vi.fn<typeof requestAiProposal>>;
  decide?: ReturnType<typeof vi.fn<typeof decideAiItem>>;
  discard?: ReturnType<typeof vi.fn<typeof deleteAiProposal>>;
  onDiscard?: () => void;
  onPlace?: (item: AiItem, proposal: AiProposal) => PlaceProblem | null;
  placed?: Set<string>;
  token?: string | null;
  description?: string;
};

function Harness({
  initial = null,
  request = vi.fn<typeof requestAiProposal>(),
  decide = vi.fn<typeof decideAiItem>(),
  discard = vi.fn<typeof deleteAiProposal>(),
  onDiscard,
  onPlace = () => null,
  placed = new Set(),
  token = 'tok',
  description: start = '',
}: Props) {
  const [description, setDescription] = useState(start);
  const [current, setCurrent] = useState<AiProposal | null>(initial);
  return (
    <>
      <span id="boards-step" tabIndex={-1} data-testid="boards-step">
        Boards
      </span>
      <AiIntake
        token={token}
        description={description}
        onDescriptionChange={setDescription}
        proposal={current}
        onProposal={setCurrent}
        request={null}
        placed={placed}
        onPlace={onPlace}
        boardNames={['MDB', 'DB1']}
        manualTargetId="boards-step"
        onDiscard={() => {
          setCurrent(null);
          onDiscard?.();
        }}
        impls={{ request, decide, discard }}
      />
    </>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const description = () => screen.getByTestId<HTMLTextAreaElement>('ai-description');

/** Acknowledge that the description goes to the AI provider (once per render). */
function agree() {
  const box = screen.getByTestId<HTMLInputElement>('ai-consent');
  if (!box.checked) fireEvent.click(box);
}

describe('describe the project', () => {
  it('says what proposals are and what is sent, and shows the limit', () => {
    renderApp(<Harness />);
    const notice = screen.getByTestId('ai-notice').textContent;
    expect(notice).toContain('unconfirmed suggestions');
    expect(notice).toContain('not designed or validated');
    expect(notice).toContain('Only the text typed here is sent to the AI provider');
    expect(notice).toContain('no files');
    expect(screen.getByTestId('ai-count').textContent).toContain('0 / 8000');
    expect(screen.getByTestId<HTMLButtonElement>('ai-request').disabled).toBe(true);
    // No file input anywhere: there is no upload path.
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });

  it('says what can and cannot be deleted, and never claims full erasure', () => {
    renderApp(<Harness />);
    const note = screen.getByTestId('ai-deletion-note').textContent;
    expect(note).toContain('proposals you have not used');
    expect(note).toContain('files already exported');
    expect(note).toContain('database backups');
    expect(note).toContain('AI provider');
    expect(note.toLowerCase()).not.toContain('permanently');
  });

  it('counts characters and refuses to send more than the limit', () => {
    renderApp(<Harness />);
    fireEvent.change(description(), { target: { value: 'x'.repeat(8001) } });
    expect(screen.getByTestId('ai-count').textContent).toContain('8001 / 8000');
    expect(screen.getByTestId('ai-count').textContent).toContain('longer than 8000');
    expect(screen.getByTestId<HTMLButtonElement>('ai-request').disabled).toBe(true);
    fireEvent.change(description(), { target: { value: 'x'.repeat(8000) } });
    // Without the acknowledgement nothing can be sent.
    expect(screen.getByTestId<HTMLButtonElement>('ai-request').disabled).toBe(true);
    agree();
    expect(screen.getByTestId<HTMLButtonElement>('ai-request').disabled).toBe(false);
  });

  it('sends the typed text and shows the proposals grouped by kind', async () => {
    const request = vi
      .fn<typeof requestAiProposal>()
      .mockResolvedValue({ kind: 'proposed', proposal: proposal() });
    renderApp(<Harness request={request} />);
    fireEvent.change(description(), { target: { value: DESCRIPTION } });
    agree();
    fireEvent.click(screen.getByTestId('ai-request'));
    await screen.findByTestId('ai-review');
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({ token: 'tok', description: DESCRIPTION, request: null }),
    );
    for (const group of ['board', 'load', 'control_need', 'question', 'unsupported']) {
      expect(screen.getByTestId(`ai-group-${group}`)).toBeTruthy();
    }
    expect(within(screen.getByTestId('ai-group-load')).getAllByRole('listitem')).toHaveLength(2);
    // Every item reads as proposed: nothing is accepted on arrival.
    for (const status of screen.getAllByTestId(/^ai-status-/)) {
      expect(status.textContent).toBe('Proposed');
    }
    expect(screen.getByTestId('ai-unconfirmed').textContent).toContain('not designed');
    expect(screen.getByTestId('ai-provider').textContent).toContain('stub-model-1');
    // A server-rejected item, with its reason.
    const rejected = screen.getByTestId('ai-group-rejected');
    expect(within(rejected).getByTestId('ai-rejected-reason-0').textContent).toBe(
      'number_not_in_span',
    );
    expect(within(rejected).queryByRole('button')).toBeNull();
  });

  it('says an unsupported item’s reason in words, never as the server’s code', () => {
    const base = proposal();
    const items = base.items.map((item) =>
      item.id === 'i-fire'
        ? { ...item, value: { text: 'Standby pump', reason: 'possibly_life_safety' } }
        : item,
    );
    renderApp(<Harness initial={{ ...base, items }} />);
    const group = screen.getByTestId('ai-group-unsupported').textContent;
    expect(group).toContain(
      'May be life-safety equipment: needs a person’s safety review'.replace('’', "'"),
    );
    expect(group).not.toContain('possibly_life_safety');
  });

  it('shows each item’s quoted source, its fields, and highlights its span', () => {
    renderApp(<Harness initial={proposal()} />);
    expect(screen.getByTestId('ai-quote-i-pump').textContent).toBe('Pump P1 7.5 kW three phase');
    expect(screen.getByTestId('ai-value-i-pump-power_kw').textContent).toBe('7.5');
    expect(screen.getByTestId('ai-value-i-pump-kind').textContent).toBe('Motor');
    // A field the item does not give is not shown as if it were.
    expect(screen.queryByTestId('ai-value-i-light-phases')).toBeNull();
    expect(screen.getByTestId('ai-value-i-start-load').textContent).toBe('Pump P1');
    expect(screen.queryByTestId('ai-highlight')).toBeNull();
    const show = screen.getByTestId('ai-show-i-pump');
    fireEvent.click(show);
    expect(show.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('ai-highlight').textContent).toBe('Pump P1 7.5 kW three phase');
    expect(screen.getByTestId('ai-original').textContent).toBe(DESCRIPTION);
  });
});

describe('deciding on an item', () => {
  it('shows nothing as accepted, and places nothing, until the server confirms', async () => {
    const answer = deferred<AiProposalOutcome>();
    const decide = vi.fn<typeof decideAiItem>().mockReturnValue(answer.promise);
    const onPlace = vi.fn(() => null);
    renderApp(<Harness initial={proposal()} decide={decide} onPlace={onPlace} />);
    fireEvent.click(screen.getByTestId('ai-accept-i-pump'));
    expect(screen.getByTestId('ai-status-i-pump').textContent).toBe('Recording…');
    expect(screen.getByTestId<HTMLButtonElement>('ai-reject-i-board').disabled).toBe(true);
    expect(onPlace).not.toHaveBeenCalled();
    expect(decide).toHaveBeenCalledWith(
      expect.objectContaining({ item: 'i-pump', decision: 'accept' }),
    );
    expect(decide.mock.calls[0]?.[0]).not.toHaveProperty('value');
    await act(async () => {
      answer.resolve({ kind: 'proposed', proposal: decided(proposal(), 'i-pump', 'accept') });
      await answer.promise;
    });
    expect(screen.getByTestId('ai-status-i-pump').textContent).toBe('Accepted');
    expect(screen.getByTestId('ai-decided-i-pump').textContent).toBe(
      'Accepted by Dana Reviewer at 2026-11-04 09:05',
    );
    expect(onPlace).toHaveBeenCalledTimes(1);
  });

  it('places nothing on a reject', async () => {
    const decide = vi
      .fn<typeof decideAiItem>()
      .mockResolvedValue({ kind: 'proposed', proposal: decided(proposal(), 'i-pump', 'reject') });
    const onPlace = vi.fn(() => null);
    renderApp(<Harness initial={proposal()} decide={decide} onPlace={onPlace} />);
    fireEvent.click(screen.getByTestId('ai-reject-i-pump'));
    await waitFor(() => {
      expect(screen.getByTestId('ai-status-i-pump').textContent).toBe('Rejected');
    });
    expect(onPlace).not.toHaveBeenCalled();
  });

  it('says why a confirmed item could not be placed, and offers to place it again', async () => {
    const decide = vi
      .fn<typeof decideAiItem>()
      .mockResolvedValue({ kind: 'proposed', proposal: decided(proposal(), 'i-light', 'accept') });
    const onPlace = vi
      .fn<(item: AiItem, p: AiProposal) => PlaceProblem | null>()
      .mockReturnValue({ code: 'missing', fields: ['phases'] });
    renderApp(<Harness initial={proposal()} decide={decide} onPlace={onPlace} />);
    fireEvent.click(screen.getByTestId('ai-accept-i-light'));
    const problem = await screen.findByTestId('ai-problem-i-light');
    expect(problem.textContent).toContain('Phases');
    expect(problem.textContent).toContain('Nothing is filled in for you');
    expect(screen.getByTestId('ai-place-i-light')).toBeTruthy();
  });

  it('edits within the kind’s fields, validated, and sends only what was given', async () => {
    const edited = decided(proposal(), 'i-light', 'edit', {
      board: 'MDB',
      description: 'Lighting',
      kind: 'lighting',
      phases: 1,
    });
    const decide = vi
      .fn<typeof decideAiItem>()
      .mockResolvedValue({ kind: 'proposed', proposal: edited });
    renderApp(<Harness initial={proposal()} decide={decide} />);
    fireEvent.click(screen.getByTestId('ai-edit-open-i-light'));
    const form = screen.getByTestId('ai-edit-i-light');
    // The board's own fields only: a load has no "fed from", no approval, no author.
    expect(within(form).queryByTestId('ai-edit-i-light-fed_from')).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId('ai-edit-i-light-board'));
    fireEvent.change(screen.getByTestId('ai-edit-i-light-power_factor'), {
      target: { value: '1.4' },
    });
    fireEvent.submit(form);
    expect(screen.getByText('At most 1.')).toBeTruthy();
    expect(decide).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId('ai-edit-i-light-power_factor'), {
      target: { value: '' },
    });
    fireEvent.change(screen.getByTestId('ai-edit-i-light-phases'), { target: { value: '1' } });
    fireEvent.click(screen.getByTestId('ai-edit-save-i-light'));
    await waitFor(() => {
      expect(screen.getByTestId('ai-status-i-light').textContent).toBe('Edited');
    });
    expect(decide.mock.calls[0]?.[0]).toMatchObject({
      decision: 'edit',
      value: { board: 'MDB', description: 'Lighting', kind: 'lighting', phases: 1 },
    });
    const sent = decide.mock.calls[0]?.[0].value ?? {};
    expect(Object.keys(sent).sort()).toEqual(['board', 'description', 'kind', 'phases']);
  });

  it('is keyboard operable: Escape leaves an edit and returns to its button', () => {
    renderApp(<Harness initial={proposal()} />);
    const open = screen.getByTestId('ai-edit-open-i-board');
    expect(open.tagName).toBe('BUTTON');
    fireEvent.click(open);
    fireEvent.keyDown(screen.getByTestId('ai-edit-i-board-name'), { key: 'Escape' });
    expect(screen.queryByTestId('ai-edit-i-board')).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId('ai-edit-open-i-board'));
    for (const id of ['ai-accept-i-board', 'ai-reject-i-board']) {
      expect(screen.getByTestId(id).tagName).toBe('BUTTON');
    }
  });

  it('locks the decision of an item placed in the editor', () => {
    renderApp(
      <Harness initial={decided(proposal(), 'i-pump', 'accept')} placed={new Set(['i-pump'])} />,
    );
    expect(screen.getByTestId('ai-placed-i-pump')).toBeTruthy();
    for (const id of ['ai-accept-i-pump', 'ai-edit-open-i-pump', 'ai-reject-i-pump']) {
      expect(screen.getByTestId<HTMLButtonElement>(id).disabled).toBe(true);
    }
  });

  it('offers no decision on a proposal a revision used', () => {
    renderApp(<Harness initial={proposal({ used_by_revision_id: 'rev-9' })} />);
    expect(screen.getByTestId('ai-used')).toBeTruthy();
    expect(screen.getByTestId<HTMLButtonElement>('ai-accept-i-board').disabled).toBe(true);
  });
});

describe('failures', () => {
  const cases: Array<[string, AiProposalOutcome, string]> = [
    ['ai_unavailable', { kind: 'refused', detail: '', code: 'ai_unavailable' }, 'not available'],
    [
      'ai_proposal_malformed',
      { kind: 'refused', detail: '', code: 'ai_proposal_malformed' },
      'could not be used',
    ],
    ['ai_timeout', { kind: 'refused', detail: '', code: 'ai_timeout' }, 'too long to answer'],
    [
      'ai_input_too_long',
      { kind: 'refused', detail: '', code: 'ai_input_too_long' },
      'longer than 8000',
    ],
    [
      'ai_input_empty',
      { kind: 'refused', detail: '', code: 'ai_input_empty' },
      'Type a description',
    ],
    [
      'ai_requires_account',
      { kind: 'refused', detail: '', code: 'ai_requires_account' },
      'signed-in account',
    ],
    [
      'ai_proposal_used',
      { kind: 'refused', detail: '', code: 'ai_proposal_used' },
      'used by a saved revision',
    ],
    ['network', { kind: 'failed' }, ''],
  ];

  it.each(cases)(
    'a %s request says so, offers Retry and Continue manually, and changes nothing',
    async (_name, outcome, words) => {
      const request = vi.fn<typeof requestAiProposal>().mockResolvedValue(outcome);
      const onPlace = vi.fn(() => null);
      renderApp(<Harness request={request} onPlace={onPlace} description="My typed text" />);
      agree();
      fireEvent.click(screen.getByTestId('ai-request'));
      const error = await screen.findByTestId('ai-error');
      expect(error.getAttribute('role')).toBe('alert');
      if (words) expect(error.textContent).toContain(words);
      expect(error.textContent).toContain('Nothing in the editor');
      expect(description().value).toBe('My typed text');
      expect(screen.queryByTestId('ai-review')).toBeNull();
      expect(onPlace).not.toHaveBeenCalled();
      // Retry sends the same text again.
      request.mockResolvedValueOnce({ kind: 'proposed', proposal: proposal() });
      fireEvent.click(screen.getByTestId('ai-retry'));
      await screen.findByTestId('ai-review');
      expect(request).toHaveBeenCalledTimes(2);
      expect(request.mock.calls[1]?.[0].description).toBe('My typed text');
    },
  );

  it('Continue manually moves to the editor and clears the message', async () => {
    const request = vi
      .fn<typeof requestAiProposal>()
      .mockResolvedValue({ kind: 'refused', detail: '', code: 'ai_timeout' });
    renderApp(<Harness request={request} description="text" />);
    agree();
    fireEvent.click(screen.getByTestId('ai-request'));
    await screen.findByTestId('ai-error');
    fireEvent.click(screen.getByTestId('ai-manual'));
    expect(screen.queryByTestId('ai-error')).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId('boards-step'));
    expect(description().value).toBe('text');
  });

  it('a failed decision keeps the item proposed, with Retry and Continue manually', async () => {
    const decide = vi
      .fn<typeof decideAiItem>()
      .mockResolvedValueOnce({ kind: 'refused', detail: '', code: 'ai_proposal_used' })
      .mockResolvedValueOnce({
        kind: 'proposed',
        proposal: decided(proposal(), 'i-board', 'accept'),
      });
    const onPlace = vi.fn(() => null);
    renderApp(<Harness initial={proposal()} decide={decide} onPlace={onPlace} />);
    fireEvent.click(screen.getByTestId('ai-accept-i-board'));
    const error = await screen.findByTestId('ai-item-error-i-board');
    expect(error.textContent).toContain('used by a saved revision');
    expect(screen.getByTestId('ai-status-i-board').textContent).toBe('Proposed');
    expect(onPlace).not.toHaveBeenCalled();
    fireEvent.click(within(error).getByText('Retry'));
    await waitFor(() => {
      expect(screen.getByTestId('ai-status-i-board').textContent).toBe('Accepted');
    });
    expect(onPlace).toHaveBeenCalledTimes(1);
  });
});

describe('untrusted proposal text', () => {
  it('is rendered as text, never as markup', () => {
    const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>';
    const hostile = proposal({
      input_text: `${evil} pump`,
      provider: '<b>prov</b>',
      model: '<i>m</i>',
      items: [
        {
          id: 'i-evil',
          kind: 'load',
          source: { start: 0, end: evil.length },
          quote: evil,
          value: { board: '<u>B</u>', description: evil, kind: 'motor' },
        },
      ],
      server_rejected: [{ quote: evil, reason: '<a href="javascript:alert(1)">x</a>' }],
    });
    renderApp(<Harness initial={hostile} />);
    fireEvent.click(screen.getByTestId('ai-show-i-evil'));
    const review = screen.getByTestId('ai-review');
    expect(review.querySelector('img, script, b, i, u, a')).toBeNull();
    expect(screen.getByTestId('ai-quote-i-evil').textContent).toBe(evil);
    expect(screen.getByTestId('ai-highlight').textContent).toBe(evil);
    expect(screen.getByTestId('ai-value-i-evil-description').textContent).toBe(evil);
    expect((window as { __pwned?: number }).__pwned).toBeUndefined();
  });
});

describe('right-to-left', () => {
  it.each(['ar', 'he'] as const)('renders in %s with the quotes isolated', (locale) => {
    const hebrewText = 'לוח ראשי MDB. משאבה 7.5 kW';
    const p = proposal({
      input_text: hebrewText,
      items: [
        {
          id: 'i-b',
          kind: 'board',
          source: { start: 0, end: 8 },
          quote: 'לוח ראשי',
          value: { name: 'MDB' },
        },
      ],
      server_rejected: [],
    });
    renderApp(<Harness initial={p} />, { locale });
    expect(document.documentElement.dir).toBe('rtl');
    expect(screen.getByTestId('ai-notice').textContent).not.toContain('unconfirmed');
    const quote = screen.getByTestId('ai-quote-i-b');
    expect(quote.tagName).toBe('BDI');
    expect(quote.getAttribute('dir')).toBe('auto');
    expect(screen.getByTestId('ai-original').getAttribute('dir')).toBe('auto');
    fireEvent.click(screen.getByTestId('ai-show-i-b'));
    expect(screen.getByTestId('ai-highlight').textContent).toBe('לוח ראשי');
    expect(screen.getByTestId('ai-description').getAttribute('dir')).toBe('auto');
  });
});

describe('the saved revision', () => {
  const RECORD: RequirementsRecord = {
    schema: 'pp-req/1',
    intake: 'ai',
    derived: false,
    boards: [{ ref: 'b1', name: 'MDB', fed_from_ref: null }],
    loads: [{ ref: 'b1.l1', board_ref: 'b1', description: 'Pump P1' }],
    feeders: [],
    acknowledgements: [],
    control: [],
    questions: [],
    open_items: [
      { code: 'ai_item_unreviewed', subject: { item_id: 'i-light' } },
      { code: 'ai_question', subject: { item_id: 'i-q' } },
      { code: 'ai_unsupported', subject: { item_id: 'i-fire' } },
    ],
    trace: [],
    ai: {
      proposal: proposal(),
      decisions: decided(decided(proposal(), 'i-board', 'accept'), 'i-pump', 'edit', {
        power_kw: 5.5,
      }).decisions,
      placements: [
        { item_id: 'i-board', board_index: 0 },
        { item_id: 'i-pump', board_index: 0, load_index: 0, changed_in_editor: ['description'] },
      ],
    },
  };

  it('lists the AI open items and its provenance', () => {
    renderApp(
      <RequirementsView
        token="tok"
        boards={[blankBoard(0, 1, 'MDB')]}
        onBoardChange={vi.fn()}
        onAddBoard={vi.fn()}
        onLoadChange={vi.fn()}
        onLoadRemove={vi.fn()}
        onLoadAdd={vi.fn()}
        control={{}}
        onControlChange={vi.fn()}
        request={null}
        opened={{ id: 'p1', revision: 2 }}
        savedRecord={RECORD}
        onProject={vi.fn()}
      />,
    );
    expect(screen.getByTestId('open-item-ai_item_unreviewed').textContent).toContain(
      'AI proposal not reviewed',
    );
    expect(screen.getByTestId('open-item-quote-i-q').textContent).toBe('“What cable length?”');
    expect(screen.getByTestId('open-item-quote-i-fire').textContent).toBe('“Fire pump FP1”');
    const provenance = screen.getByTestId('ai-provenance');
    expect(within(provenance).getByTestId('ai-provenance-text').textContent).toBe(DESCRIPTION);
    expect(within(provenance).getByTestId('ai-provenance-provider').textContent).toContain(
      'stub · stub-model-1',
    );
    const decisions = within(provenance).getByTestId('ai-provenance-decisions').textContent;
    expect(decisions).toContain('Accepted by Dana Reviewer at 2026-11-04 09:05');
    expect(decisions).toContain('Edited by Dana Reviewer');
    expect(decisions).toContain('changed in the editor: description');
    expect(provenance.textContent).toContain('not engineer-validated');
  });

  it('keeps the life-safety mention open on the saved record', () => {
    renderApp(
      <RequirementsView
        token="tok"
        boards={[blankBoard(0, 1, 'MDB')]}
        onBoardChange={vi.fn()}
        onAddBoard={vi.fn()}
        onLoadChange={vi.fn()}
        onLoadRemove={vi.fn()}
        onLoadAdd={vi.fn()}
        control={{}}
        onControlChange={vi.fn()}
        request={null}
        opened={{ id: 'p1', revision: 2 }}
        savedRecord={{
          ...RECORD,
          open_items: [{ code: 'ai_life_safety_mentioned', subject: {} }],
        }}
        onProject={vi.fn()}
      />,
    );
    expect(screen.getByTestId('open-item-ai_life_safety_mentioned').textContent).toContain(
      'Description mentions life-safety equipment',
    );
  });
});

describe('a description that mentions life-safety equipment', () => {
  it('says so beside the proposals, and offers no way to dismiss it', () => {
    renderApp(<Harness initial={proposal({ life_safety_mentioned: true })} />);
    const notice = screen.getByTestId('ai-life-safety');
    expect(notice.textContent).toContain('fire-pump or life-safety equipment');
    expect(notice.textContent).toContain('not designed');
    expect(within(notice).queryByRole('button')).toBeNull();
  });

  it('shows nothing when the description does not', () => {
    renderApp(<Harness initial={proposal()} />);
    expect(screen.queryByTestId('ai-life-safety')).toBeNull();
  });
});

describe('consent, per-account limits and deleting a proposal (S2/S3)', () => {
  it('sends nothing until the user acknowledges the AI provider, then sends the acknowledgement', async () => {
    const request = vi
      .fn<typeof requestAiProposal>()
      .mockResolvedValue({ kind: 'proposed', proposal: { ...proposal(), calls_remaining: 4 } });
    renderApp(<Harness request={request} description="text" />);
    expect(screen.getByTestId('ai-consent-label').textContent).toContain('sent to the AI provider');
    expect(screen.getByTestId<HTMLButtonElement>('ai-request').disabled).toBe(true);
    agree();
    fireEvent.click(screen.getByTestId('ai-request'));
    await screen.findByTestId('ai-review');
    expect(request).toHaveBeenCalledWith(expect.objectContaining({ consent: true }));
    expect(screen.getByTestId('ai-remaining').textContent).toContain('4 AI proposal requests left');
  });

  it.each([
    [
      {
        kind: 'refused',
        detail: '',
        code: 'ai_user_limit_exceeded',
        params: { limit: '100', period: '2026-11' },
      },
      'used your 100 AI proposal requests for 2026-11',
    ],
    [{ kind: 'refused', detail: '', code: 'ai_request_in_flight' }, 'still being prepared'],
    [
      { kind: 'refused', detail: '', code: 'ai_consent_required' },
      'may be sent to the AI provider',
    ],
  ] as Array<[AiProposalOutcome, string]>)('says %o in its own words', async (outcome, words) => {
    const request = vi.fn<typeof requestAiProposal>().mockResolvedValue(outcome);
    renderApp(<Harness request={request} description="text" />);
    agree();
    fireEvent.click(screen.getByTestId('ai-request'));
    const error = await screen.findByTestId('ai-error');
    expect(error.textContent).toContain(words);
    expect(description().value).toBe('text');
  });

  it('deletes an unused proposal and forgets it; a used one offers no delete', async () => {
    const discard = vi.fn<typeof deleteAiProposal>().mockResolvedValue({ kind: 'deleted' });
    const onDiscard = vi.fn();
    renderApp(<Harness initial={proposal()} discard={discard} onDiscard={onDiscard} />);
    expect(screen.getByTestId('ai-review').textContent).toContain('provider keeps');
    fireEvent.click(screen.getByTestId('ai-discard'));
    await waitFor(() => {
      expect(onDiscard).toHaveBeenCalledTimes(1);
    });
    expect(discard).toHaveBeenCalledWith(
      expect.objectContaining({ token: 'tok', id: proposal().id }),
    );
    expect(screen.queryByTestId('ai-review')).toBeNull();
  });

  it('a refused delete says why and keeps the proposal', async () => {
    const discard = vi
      .fn<typeof deleteAiProposal>()
      .mockResolvedValue({ kind: 'refused', detail: '', code: 'ai_proposal_not_yours' });
    renderApp(<Harness initial={proposal()} discard={discard} />);
    fireEvent.click(screen.getByTestId('ai-discard'));
    expect((await screen.findByTestId('ai-error')).textContent).toContain('Only the person');
    expect(screen.getByTestId('ai-review')).toBeTruthy();
  });

  it('a used proposal cannot be deleted from here', () => {
    renderApp(<Harness initial={{ ...proposal(), used_by_revision_id: 'r1' }} />);
    expect(screen.queryByTestId('ai-discard')).toBeNull();
  });
});
