import { fireEvent, screen, waitFor } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { describe, expect, it, vi } from 'vitest';

import { Chat } from '@/components/chat';
import type { fetchSession, listSessions } from '@/lib/sessions';

import { renderApp } from './helpers';

/**
 * FE-011's acceptance criterion, end to end through the real `Chat`.
 *
 * > Selecting a past session restores its context indicator and message
 * > history correctly.
 *
 * Both halves, and the second is the one that fails quietly. The messages are
 * visible immediately, so a regression there is obvious; the context indicator
 * is a small chip, and coming back blank looks like a design choice rather
 * than a bug — until the engineer's next question goes out without the
 * equipment attached and gets an answer about the wrong machine.
 *
 * Driven through the component rather than the reducer, because the reducer
 * only restores messages: the chip is separate state that `openSession` has to
 * set, and a test at the reducer level would pass while the chip stayed empty.
 */

type DiagnosticTurn = components['schemas']['DiagnosticTurn'];

const CITATION = {
  document_id: 'doc-1',
  document_title: 'ACS880 Firmware Manual',
  manufacturer: 'ABB',
  page: 214,
  section: '6.3',
};

function storedTurn(question: string, model: string | null): DiagnosticTurn {
  return {
    request: { session_id: 'old-session', symptom: question, locale: 'en' },
    response: {
      session_id: 'old-session',
      answer: { text: `Answer to ${question}`, citations: [CITATION] },
      diagnosis: {
        summary: `Summary for ${question}`,
        summary_citation_ids: ['doc-1'],
        severity: 'info',
        equipment_model: model,
        steps: [
          {
            order: 1,
            instruction: `Answer to ${question}`,
            rationale: 'Recorded from an earlier turn.',
            citation_ids: ['doc-1'],
            severity: 'info',
          },
        ],
      },
      confidence: {
        overall: 0.8,
        retrieval_score: 0.8,
        passage_agreement: 0.8,
        citation_density: 0.8,
      },
      low_confidence: false,
    },
  };
}

function history(): typeof listSessions {
  return vi.fn().mockResolvedValue({
    kind: 'loaded',
    sessions: [
      {
        id: 'old-session',
        title: 'ACS880 undervoltage trip',
        equipmentModel: 'ACS880',
        turnCount: 2,
        createdAt: '2026-01-01T00:00:00Z',
        updatedAt: '2026-01-02T00:00:00Z',
      },
    ],
    nextCursor: null,
  });
}

function stored(turns: DiagnosticTurn[]): typeof fetchSession {
  return vi.fn().mockResolvedValue({
    kind: 'loaded',
    session: { id: 'old-session', turns },
  });
}

async function openStoredSession(turns: DiagnosticTurn[], fetchImpl?: typeof fetchSession) {
  const view = renderApp(
    <Chat token="t" listImpl={history()} fetchSessionImpl={fetchImpl ?? stored(turns)} />,
  );
  fireEvent.click(await screen.findByTestId('history-item-old-session'));
  return view;
}

describe('selecting a past session', () => {
  it('restores the message history', async () => {
    await openStoredSession([storedTurn('why is it tripping?', 'ACS880')]);

    expect(await screen.findByText('why is it tripping?')).not.toBeNull();
  });

  it('restores the context indicator', async () => {
    // The half that fails quietly. `equipment_model` is not derivable from the
    // stored prose, so this only passes because the API records what was shown
    // and `openSession` puts it back on the chip.
    await openStoredSession([storedTurn('why is it tripping?', 'ACS880')]);

    await waitFor(() => {
      expect(screen.getByTestId('context-chip').textContent).toContain('ACS880');
    });
  });

  it('restores the most recent equipment when the session changed machines', async () => {
    // An engineer who moved to another unit mid-session is looking for the
    // unit they moved to, not the one they started on.
    await openStoredSession([
      storedTurn('first fault', 'VLT2800'),
      storedTurn('second fault', 'ACS880'),
    ]);

    await waitFor(() => {
      expect(screen.getByTestId('context-chip').textContent).toContain('ACS880');
    });
  });

  it('does not let a later turn without equipment blank the chip', async () => {
    // "and what about the fan?" identifies no machine, and must not erase a
    // context the conversation had already established.
    await openStoredSession([
      storedTurn('ACS880 trips on start', 'ACS880'),
      storedTurn('and what about the fan?', null),
    ]);

    await waitFor(() => {
      expect(screen.getByTestId('context-chip').textContent).toContain('ACS880');
    });
  });

  it('leaves the chip neutral when no turn identified equipment', async () => {
    // Rather than guessing one out of the answer text, which is exactly what
    // the chip's neutral state exists to prevent.
    await openStoredSession([storedTurn('something vague', null)]);

    await screen.findByText('something vague');
    // Scoped to the chip: the sidebar row legitimately prints the model, so a
    // page-wide query here would pass no matter what the chip did.
    expect(screen.getByTestId('context-chip').textContent).not.toContain('ACS880');
  });

  it('fetches through the same session route the chat view loads from', async () => {
    const impl = stored([storedTurn('q', 'ACS880')]);
    await openStoredSession([], impl);

    await waitFor(() => {
      expect(impl).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'old-session' }));
    });
  });

  it('leaves the transcript alone when the session cannot be loaded', async () => {
    // Better an unchanged view than a half-cleared one: blanking the
    // transcript on a failed fetch would lose the conversation the engineer
    // was already reading.
    const failing = vi.fn().mockResolvedValue({ kind: 'failed' });

    renderApp(<Chat token="t" listImpl={history()} fetchSessionImpl={failing} />);
    fireEvent.click(await screen.findByTestId('history-item-old-session'));

    await waitFor(() => {
      expect(failing).toHaveBeenCalled();
    });
    expect(screen.queryByText('why is it tripping?')).toBeNull();
  });
});
