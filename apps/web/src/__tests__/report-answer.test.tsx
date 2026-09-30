import { fireEvent, screen } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { describe, expect, it, vi } from 'vitest';

import { Chat } from '@/components/chat';
import type { StreamEvent } from '@/lib/diagnosis-stream';
import { flagAnswer, type FlagOutcome } from '@/lib/feedback';

import { renderApp } from './helpers';

/**
 * Reporting an answer as wrong.
 *
 * `POST /feedback/flag` existed with nothing in the product able to call it:
 * the answer carried no id to name, and no control sent one.
 */

type DiagnosticResponse = components['schemas']['DiagnosticResponse'];
type RetrievedPassage = components['schemas']['RetrievedPassage'];

const PASSAGE: RetrievedPassage = {
  id: 'p1',
  text: 'F0001 overcurrent.',
  score: 0.9,
  similarity: 0.7,
  anchored: true,
  citation: {
    document_id: 'doc-1',
    document_title: 'ACS880 Firmware Manual',
    manufacturer: 'ABB',
    page: 214,
    section: '6.3',
  },
};

const RESPONSE: DiagnosticResponse = {
  session_id: 'session-9',
  turn_id: 'turn-1',
  evidence: [PASSAGE],
  answer: { text: 'Undervoltage.', citations: [PASSAGE.citation] },
  diagnosis: {
    summary: 'The drive tripped on DC bus undervoltage.',
    summary_citation_ids: ['doc-1'],
    severity: 'critical',
    equipment_model: 'ACS880',
    steps: [
      {
        order: 1,
        instruction: 'Measure the supply voltage at the input terminals.',
        rationale: 'An undervoltage trip most often follows a supply fault.',
        citation_ids: ['doc-1'],
        severity: 'critical',
      },
    ],
  },
  confidence: { overall: 0.9, retrieval_score: 0.9, passage_agreement: 0.9, citation_density: 0.9 },
  low_confidence: false,
  refusal_message: null,
};

function answering(response: DiagnosticResponse) {
  // eslint-disable-next-line @typescript-eslint/require-await
  return async function* stream(): AsyncGenerator<StreamEvent> {
    yield { kind: 'result', response };
  };
}

async function ask(
  response: DiagnosticResponse,
  flagImpl: typeof flagAnswer,
  onUnauthorized?: () => void,
) {
  renderApp(
    <Chat
      token="tok"
      streamImpl={answering(response)}
      flagImpl={flagImpl}
      listImpl={vi.fn().mockResolvedValue({ kind: 'loaded', sessions: [] })}
      {...(onUnauthorized ? { onUnauthorized } : {})}
    />,
  );
  fireEvent.change(screen.getByLabelText(/describe the fault/i), {
    target: { value: 'Tripping on start' },
  });
  fireEvent.submit(
    screen.getByRole('button', { name: /send/i }).closest('form') as HTMLFormElement,
  );
  await screen.findByTestId('diagnostic-card');
}

async function report(reason: string) {
  fireEvent.click(await screen.findByRole('button', { name: 'Report a problem with this answer' }));
  fireEvent.change(screen.getByLabelText('What is wrong? (optional)'), {
    target: { value: reason },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Send report' }));
}

describe('reporting an answer', () => {
  it('sends the turn, the reason and the passages the answer was built on', async () => {
    const flagImpl = vi.fn().mockResolvedValue({ kind: 'sent' } satisfies FlagOutcome);
    await ask(RESPONSE, flagImpl);

    await report('  Wrong parameter  ');

    expect((await screen.findByTestId('report-sent')).textContent).toMatch(/reviewer/);
    expect(flagImpl).toHaveBeenCalledWith({
      token: 'tok',
      turnId: 'turn-1',
      reason: '  Wrong parameter  ',
      evidence: [PASSAGE],
    });
    expect(screen.queryByTestId('report-open')).toBeNull();
  });

  it('is not offered for an answer that was never stored', async () => {
    const { turn_id: _unused, ...unstored } = RESPONSE;
    await ask(unstored, vi.fn());
    expect(screen.queryByTestId('report-open')).toBeNull();
  });

  it('keeps the form and says so when sending failed', async () => {
    const flagImpl = vi
      .fn()
      .mockResolvedValueOnce({ kind: 'failed' })
      .mockResolvedValueOnce({ kind: 'sent' });
    await ask(RESPONSE, flagImpl);

    await report('Wrong');
    expect((await screen.findByTestId('report-error')).textContent).toMatch(/could not be sent/);

    fireEvent.click(screen.getByRole('button', { name: 'Send report' }));
    await screen.findByTestId('report-sent');
  });

  it('renews an expired session', async () => {
    const onUnauthorized = vi.fn();
    await ask(RESPONSE, vi.fn().mockResolvedValue({ kind: 'unauthorized' }), onUnauthorized);

    await report('');
    await screen.findByTestId('report-error');
    expect(onUnauthorized).toHaveBeenCalled();
  });

  it('can be closed without sending', async () => {
    const flagImpl = vi.fn();
    await ask(RESPONSE, flagImpl);
    fireEvent.click(await screen.findByTestId('report-open'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('report-form')).toBeNull();
    expect(flagImpl).not.toHaveBeenCalled();
  });
});

describe('flagAnswer', () => {
  function respond(status: number): typeof fetch {
    return vi.fn().mockResolvedValue({ ok: status >= 200 && status < 300, status });
  }

  it('posts the turn id, trimmed reason and passages with the token', async () => {
    const fetchImpl = respond(201);
    expect(
      await flagAnswer({
        token: 't',
        turnId: 'turn-1',
        reason: '  wrong  ',
        evidence: [PASSAGE],
        fetchImpl,
      }),
    ).toEqual({ kind: 'sent' });
    expect(fetchImpl).toHaveBeenCalledWith('/api/v1/feedback/flag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t' },
      body: JSON.stringify({ message_id: 'turn-1', reason: 'wrong', retrieved: [PASSAGE] }),
    });
  });

  it('sends no reason rather than an empty one', async () => {
    const fetchImpl = respond(201);
    await flagAnswer({ token: 't', turnId: 'x', reason: '   ', fetchImpl });
    const body = vi.mocked(fetchImpl).mock.calls[0]?.[1]?.body as string;
    expect(JSON.parse(body) as unknown).toEqual({ message_id: 'x', reason: null, retrieved: [] });
  });

  it.each([
    [401, 'unauthorized'],
    [404, 'not-found'],
    [500, 'failed'],
  ])('maps %i to %s', async (status, kind) => {
    expect(await flagAnswer({ token: 't', turnId: 'x', fetchImpl: respond(status) })).toEqual({
      kind,
    });
  });

  it('reports a network failure as failed', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('offline'));
    expect(await flagAnswer({ token: 't', turnId: 'x', fetchImpl })).toEqual({ kind: 'failed' });
  });
});
