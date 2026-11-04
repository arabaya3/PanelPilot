import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderApp } from './helpers';

import { RequirementsView } from '@/components/design/requirements-view';
import {
  clarifySafetyMention,
  safetyClarificationOf,
  type ProjectDesignRequest,
  type RequirementsRecord,
} from '@/lib/design';

/** M6c: clarifying a (possibly) life-safety mention as not life-safety, four-eyes, server-flagged. */

const REQUEST = { info: { name: 'Tower' }, boards: [] } as unknown as ProjectDesignRequest;
const MENTION = 'm0123456789abcdef';

function record(overrides: Partial<RequirementsRecord> = {}): RequirementsRecord {
  return {
    schema: 'pp-req/1',
    intake: 'ai',
    derived: false,
    boards: [{ ref: 'b1', name: 'MDB', fed_from_ref: null }],
    loads: [],
    feeders: [],
    acknowledgements: [],
    control: [],
    questions: [],
    open_items: [
      { code: 'ai_possibly_life_safety', subject: { mention_id: MENTION, term: 'emergency' } },
    ],
    trace: [],
    ai: {
      proposal: { id: 'p', input_text: 'Emergency contact on the label.', items: [] },
      decisions: [],
      placements: [],
    },
    ...overrides,
  };
}

const CLARIFIED = record({
  open_items: [],
  ai: {
    proposal: { id: 'p', input_text: 'Emergency contact on the label.', items: [] },
    clarifications: [
      {
        mention_id: MENTION,
        statement: 'not_life_safety',
        reason: 'A phone number on the door label.',
        by_user_id: 'u2',
        by_name: 'Rana Haddad',
        at: '2026-11-04T10:00:00Z',
      },
    ],
  },
});

function view(
  props: Partial<Parameters<typeof RequirementsView>[0]> = {},
  locale: 'en' | 'ar' | 'he' = 'en',
) {
  renderApp(
    <RequirementsView
      token="tok"
      boards={[]}
      onBoardChange={vi.fn()}
      onAddBoard={vi.fn()}
      onLoadChange={vi.fn()}
      onLoadRemove={vi.fn()}
      onLoadAdd={vi.fn()}
      control={{}}
      onControlChange={vi.fn()}
      request={REQUEST}
      opened={{ id: 'p1', revision: 1 }}
      savedRecord={record()}
      onProject={vi.fn()}
      {...props}
    />,
    { locale },
  );
}

describe('clarifying a safety mention', () => {
  it('shows the mention and its term, open', () => {
    view({ safetyClarification: { eligible: true, reason: null } });
    expect(screen.getByTestId('open-item-ai_possibly_life_safety').textContent).toContain(
      'Description may refer to life-safety equipment',
    );
    expect(screen.getByTestId(`open-item-term-${MENTION}`).textContent).toBe('“emergency”');
  });

  it('offers the action to an eligible member only, with a required reason of 10 to 500 characters', async () => {
    const clarify = vi
      .fn<typeof clarifySafetyMention>()
      .mockResolvedValue({ kind: 'recorded', requirements: CLARIFIED });
    view({ safetyClarification: { eligible: true, reason: null }, impls: { clarify } });
    fireEvent.click(screen.getByTestId(`clarify-open-${MENTION}`));
    const submit = screen.getByTestId<HTMLButtonElement>(`clarify-submit-${MENTION}`);
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByTestId(`clarify-reason-${MENTION}`), {
      target: { value: '   short   ' },
    });
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByTestId(`clarify-reason-${MENTION}`), {
      target: { value: '  A phone number on the door label.  ' },
    });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => {
      expect(screen.getByTestId(`clarified-${MENTION}`).textContent).toContain(
        'Judged not life-safety by Rana Haddad on 2026-11-04:',
      );
    });
    // The page sends the mention, the statement's reason and nothing of who or when.
    expect(clarify.mock.calls[0]?.[0]).toMatchObject({
      id: 'p1',
      revision: 1,
      mention: MENTION,
      reason: 'A phone number on the door label.',
    });
    expect(clarify.mock.calls[0]?.[0]).not.toHaveProperty('by_name');
    expect(screen.getByTestId(`clarified-${MENTION}`).textContent).toContain(
      'A phone number on the door label.',
    );
    expect(screen.queryByTestId('open-item-ai_possibly_life_safety')).toBeNull();
    expect(screen.getByTestId('requirements-open-items').textContent).toContain(
      'not engineer validation',
    );
  });

  it.each([
    ['not_permitted', 'Only a member who may approve revisions'],
    ['proposal_author', 'You created this AI proposal'],
    ['safety_concern_unresolved', 'An item of this proposal is life-safety equipment'],
    ['revision_already_approved', 'This revision is approved'],
  ])('says why an ineligible member cannot clarify (%s)', (reason, text) => {
    view({ safetyClarification: { eligible: false, reason } });
    expect(screen.queryByTestId(`clarify-open-${MENTION}`)).toBeNull();
    expect(screen.getByTestId(`clarify-ineligible-${MENTION}`).textContent).toContain(text);
  });

  it('offers nothing without the server flag', () => {
    view({ safetyClarification: null });
    expect(screen.queryByTestId(`clarify-open-${MENTION}`)).toBeNull();
    expect(screen.queryByTestId(`clarify-ineligible-${MENTION}`)).toBeNull();
  });

  it('says a refusal the server answers with', async () => {
    const clarify = vi.fn<typeof clarifySafetyMention>().mockResolvedValue({
      kind: 'refused',
      code: 'clarification_by_proposal_author',
      detail: '',
    } as never);
    view({ safetyClarification: { eligible: true, reason: null }, impls: { clarify } });
    fireEvent.click(screen.getByTestId(`clarify-open-${MENTION}`));
    fireEvent.change(screen.getByTestId(`clarify-reason-${MENTION}`), {
      target: { value: 'A phone number on the door label.' },
    });
    fireEvent.click(screen.getByTestId(`clarify-submit-${MENTION}`));
    await waitFor(() => {
      expect(screen.getByTestId('requirements-message').textContent).toContain(
        'another member must clarify',
      );
    });
  });

  it.each([
    ['ar', 'توضيح: ليست من معدات سلامة الأرواح'],
    ['he', 'הבהרה: לא ציוד בטיחות חיים'],
  ] as const)('is translated (%s)', (locale, label) => {
    view({ safetyClarification: { eligible: true, reason: null } }, locale);
    expect(screen.getByTestId(`clarify-open-${MENTION}`).textContent).toBe(label);
  });
});

describe('the server flag and the request', () => {
  it('reads the flag from a saved project', () => {
    expect(
      safetyClarificationOf({ safety_clarification: { eligible: true, reason: null } }),
    ).toEqual({ eligible: true, reason: null });
    expect(
      safetyClarificationOf({ safety_clarification: { eligible: 'yes', reason: 'not_permitted' } }),
    ).toEqual({ eligible: false, reason: 'not_permitted' });
    expect(safetyClarificationOf({})).toBeNull();
  });

  it('posts the mention, the statement and the reason only', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ requirements: CLARIFIED }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    const outcome = await clarifySafetyMention({
      token: 'tok',
      fetchImpl,
      id: 'p1',
      revision: 3,
      mention: MENTION,
      reason: 'A phone number on the door label.',
    });
    expect(outcome.kind).toBe('recorded');
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url as string).toContain(
      '/design/projects/p1/revisions/3/requirements/safety-clarifications',
    );
    expect(JSON.parse(init?.body as string)).toEqual({
      mention_id: MENTION,
      statement: 'not_life_safety',
      reason: 'A phone number on the door label.',
    });
  });
});
