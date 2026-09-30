import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { describe, expect, it, vi } from 'vitest';

import { ReviewScreen } from '@/components/review-screen';
import type { fetchQueue } from '@/lib/verification';
import { fetchEscalations, resolveEscalation } from '@/lib/verification';

import { renderApp } from './helpers';

/**
 * A lead settling escalated items.
 *
 * `GET /verification/escalations` listed them and nothing could resolve one:
 * an incorrect or uncertain label was a one-way door.
 */

type QueueItem = components['schemas']['QueueItem'];

const CRAWLED: QueueItem = {
  id: 'esc-1',
  chunk_id: 'c1',
  status: 'escalated',
  assigned_at: '2026-09-30T06:00:00Z',
  content: 'S201 B16: 16 A at 40 C.',
  source_url: 'https://library.abb.com/s200.pdf',
  page: 27,
  section: '4.2',
  origin: 'crawl',
  label: 'incorrect',
  note: 'The table says 30 C, not 40 C.',
  assigned_to_you: false,
};

const MINE: QueueItem = {
  ...CRAWLED,
  id: 'esc-2',
  label: 'uncertain',
  note: 'Cannot tell which column applies.',
  assigned_to_you: true,
};

function renderReview({
  escalations = [CRAWLED, MINE],
  resolveImpl = vi.fn().mockResolvedValue(undefined),
  fetchQueueImpl = vi.fn().mockResolvedValue({ kind: 'loaded', items: [] }),
}: {
  escalations?: QueueItem[];
  resolveImpl?: typeof resolveEscalation;
  fetchQueueImpl?: typeof fetchQueue;
} = {}) {
  renderApp(
    <ReviewScreen
      fetchQueueImpl={fetchQueueImpl}
      fetchStaleImpl={vi.fn().mockResolvedValue({ kind: 'loaded', items: [] })}
      fetchEscalationsImpl={vi.fn().mockResolvedValue({ kind: 'loaded', items: escalations })}
      resolveEscalationImpl={resolveImpl}
      signInImpl={vi
        .fn()
        .mockResolvedValue({ kind: 'signed-in', accessToken: 'tok', refreshToken: 'r' })}
    />,
  );
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'lead@example.com' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
  fireEvent.submit(screen.getByTestId('sign-in-form'));
}

async function openTab() {
  fireEvent.click(await screen.findByTestId('review-tab-escalations'));
}

describe('the escalated tab', () => {
  it('counts the escalations and shows each with the verifier’s label and note', async () => {
    renderReview();
    expect((await screen.findByTestId('review-tab-escalations')).textContent).toContain('2');
    await openTab();

    const card = screen.getByTestId('escalation-esc-1');
    expect(card.textContent).toContain('Incorrect');
    expect(within(card).getByTestId('escalation-note').textContent).toBe(
      'The table says 30 C, not 40 C.',
    );
    expect(card.textContent).toContain('S201 B16: 16 A at 40 C.');
  });

  it('upholds with a note, and the item leaves the list', async () => {
    const resolveImpl = vi.fn().mockResolvedValue(undefined);
    renderReview({ resolveImpl });
    await openTab();

    const card = screen.getByTestId('escalation-esc-1');
    const uphold = within(card).getByRole('button', { name: 'Uphold' });
    expect((uphold as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(card).getByLabelText('Your resolution (required)'), {
      target: { value: '  Agreed.  ' },
    });
    fireEvent.click(uphold);

    await waitFor(() => {
      expect(screen.queryByTestId('escalation-esc-1')).toBeNull();
    });
    expect(resolveImpl).toHaveBeenCalledWith({
      token: 'tok',
      id: 'esc-1',
      outcome: 'upheld',
      note: 'Agreed.',
    });
    expect(screen.getByTestId('review-tab-escalations').textContent).toContain('1');
  });

  it('taking one over brings it into the lead’s own queue', async () => {
    const fetchQueueImpl = vi
      .fn()
      .mockResolvedValueOnce({ kind: 'loaded', items: [] })
      .mockResolvedValueOnce({
        kind: 'loaded',
        items: [{ ...CRAWLED, status: 'pending', label: null, note: null, assigned_to_you: true }],
      });
    renderReview({ fetchQueueImpl });
    await openTab();

    const card = screen.getByTestId('escalation-esc-1');
    fireEvent.change(within(card).getByLabelText('Your resolution (required)'), {
      target: { value: 'Verifier read the wrong column.' },
    });
    fireEvent.click(within(card).getByRole('button', { name: 'Take it over' }));

    await waitFor(() => {
      expect(screen.getByTestId('review-tab-queue').textContent).toContain('1');
    });
  });

  it('shows one’s own escalation without the controls, and says why', async () => {
    renderReview();
    await openTab();

    const card = screen.getByTestId('escalation-esc-2');
    expect(within(card).getByTestId('escalation-yours')).toBeTruthy();
    expect(within(card).queryByRole('button')).toBeNull();
  });

  it('keeps the item and shows the server’s reason when refused', async () => {
    renderReview({
      resolveImpl: vi.fn().mockRejectedValue(new Error('item esc-1 is upheld, not escalated')),
    });
    await openTab();

    const card = screen.getByTestId('escalation-esc-1');
    fireEvent.change(within(card).getByLabelText('Your resolution (required)'), {
      target: { value: 'n' },
    });
    fireEvent.click(within(card).getByRole('button', { name: 'Uphold' }));

    expect((await within(card).findByRole('alert')).textContent).toBe(
      'item esc-1 is upheld, not escalated',
    );
  });

  it('says so when there is nothing to resolve', async () => {
    renderReview({ escalations: [] });
    await openTab();
    expect(screen.getByTestId('escalations-empty')).toBeTruthy();
  });
});

describe('the escalation client', () => {
  it('reads the escalations endpoint', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ items: [CRAWLED] }),
    });
    expect(await fetchEscalations({ token: 't', fetchImpl })).toEqual({
      kind: 'loaded',
      items: [CRAWLED],
    });
    expect(fetchImpl).toHaveBeenCalledWith('/api/v1/verification/escalations', {
      headers: { Authorization: 'Bearer t' },
    });
  });

  it('posts the outcome and note, and throws the server’s reason', async () => {
    const ok = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    await resolveEscalation({
      token: 't',
      id: 'e/1',
      outcome: 'taken-over',
      note: 'n',
      fetchImpl: ok,
    });
    expect(ok).toHaveBeenCalledWith('/api/v1/verification/escalations/e%2F1/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t' },
      body: JSON.stringify({ outcome: 'taken-over', note: 'n' }),
    });

    const refused = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ detail: 'another lead must' }),
    });
    await expect(
      resolveEscalation({ token: 't', id: 'e', outcome: 'upheld', note: 'n', fetchImpl: refused }),
    ).rejects.toThrow('another lead must');
  });
});
