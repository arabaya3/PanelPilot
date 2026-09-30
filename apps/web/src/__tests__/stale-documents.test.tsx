import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ReviewScreen } from '@/components/review-screen';
import { StaleDocuments } from '@/components/verification/stale-documents';
import { dismissStale, fetchStale, retractStale } from '@/lib/verification';

import { renderApp } from './helpers';

/**
 * The reviewer's view of live documents that changed upstream.
 *
 * The worker flagged them into a table nobody could see: the only record was
 * the job's own output in a scheduler log.
 */

const CHANGED = {
  id: 'flag-1',
  source_url: 'https://library.abb.com/acs880.pdf',
  source_id: 'abb',
  reason: 'superseded',
  status: 'open',
  published_hashes: ['h1'],
  upstream_hash: 'h2',
  first_flagged_at: '2026-09-28T06:00:00Z',
  last_checked_at: '2026-09-30T06:00:00Z',
  reviewed_at: null,
  review_note: null,
};

const GONE = {
  ...CHANGED,
  id: 'flag-2',
  source_url: 'https://library.abb.com/old.pdf',
  reason: 'withdrawn',
  upstream_hash: null,
};

function respond(status: number, body: unknown = {}): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

// --- the list -----------------------------------------------------------------

describe('StaleDocuments', () => {
  it('names each change and links the source as it is now', () => {
    renderApp(<StaleDocuments items={[CHANGED, GONE]} onDismiss={vi.fn()} onRetract={vi.fn()} />);

    const changed = screen.getByTestId('stale-flag-1');
    expect(within(changed).getByText('Changed upstream')).toBeTruthy();
    const link = within(changed).getByRole('link');
    expect(link.getAttribute('href')).toBe(CHANGED.source_url);
    // A URL keeps its order inside Arabic or Hebrew text.
    expect(link.getAttribute('dir')).toBe('ltr');
    expect(within(screen.getByTestId('stale-flag-2')).getByText('Withdrawn upstream')).toBeTruthy();
    // The date label is filled, not printed as its key.
    expect(changed.textContent).toMatch(/Since .*2026/);
  });

  it('says so when nothing has changed', () => {
    renderApp(<StaleDocuments items={[]} onDismiss={vi.fn()} onRetract={vi.fn()} />);
    expect(screen.getByTestId('stale-empty').textContent).toMatch(/No live document/);
  });

  it('will not dismiss without a note', () => {
    const onDismiss = vi.fn();
    renderApp(<StaleDocuments items={[CHANGED]} onDismiss={onDismiss} onRetract={vi.fn()} />);

    const button = screen.getByRole('button', { name: 'Dismiss' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Your note (required for either decision)'), {
      target: { value: '   ' },
    });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the server’s reason when a dismissal is refused', async () => {
    const onDismiss = vi.fn().mockRejectedValue(new Error('flag flag-1 is dismissed, not open'));
    renderApp(<StaleDocuments items={[CHANGED]} onDismiss={onDismiss} onRetract={vi.fn()} />);

    fireEvent.change(screen.getByLabelText('Your note (required for either decision)'), {
      target: { value: ' cover page only ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

    expect((await screen.findByRole('alert')).textContent).toBe(
      'flag flag-1 is dismissed, not open',
    );
    expect(onDismiss).toHaveBeenCalledWith('flag-1', 'cover page only');
  });
});

describe('retracting from the list', () => {
  const NOTE = 'Your note (required for either decision)';

  it('asks for confirmation before retracting, and a note first', async () => {
    const onRetract = vi.fn().mockResolvedValue(undefined);
    renderApp(<StaleDocuments items={[CHANGED]} onDismiss={vi.fn()} onRetract={onRetract} />);

    const retract = screen.getByRole('button', { name: 'Retract from live answers' });
    expect((retract as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(NOTE), { target: { value: ' withdrawn by ABB ' } });
    fireEvent.click(retract);

    // One click only opens the confirmation; nothing is sent yet.
    expect(onRetract).not.toHaveBeenCalled();
    expect(screen.getByRole('group').textContent).toMatch(/leaves live answers now/);
    fireEvent.click(screen.getByRole('button', { name: 'Yes, retract now' }));

    await waitFor(() => {
      expect(onRetract).toHaveBeenCalledWith('flag-1', 'withdrawn by ABB');
    });
  });

  it('can be backed out of', () => {
    const onRetract = vi.fn();
    renderApp(<StaleDocuments items={[CHANGED]} onDismiss={vi.fn()} onRetract={onRetract} />);

    fireEvent.change(screen.getByLabelText(NOTE), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Retract from live answers' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('group')).toBeNull();
    expect(onRetract).not.toHaveBeenCalled();
  });

  it('says so when a retraction fails, and closes the confirmation', async () => {
    const onRetract = vi.fn().mockRejectedValue(new Error(''));
    renderApp(<StaleDocuments items={[CHANGED]} onDismiss={vi.fn()} onRetract={onRetract} />);

    fireEvent.change(screen.getByLabelText(NOTE), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Retract from live answers' }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, retract now' }));

    expect((await screen.findByRole('alert')).textContent).toBe(
      'Nothing was retracted. Try again.',
    );
    expect(screen.queryByRole('group')).toBeNull();
  });
});

// --- on the review page -------------------------------------------------------

describe('the review page’s second tab', () => {
  function renderReview(
    fetchStaleImpl: typeof fetchStale,
    dismissStaleImpl: typeof dismissStale = vi.fn().mockResolvedValue(undefined),
  ) {
    renderApp(
      <ReviewScreen
        fetchQueueImpl={vi.fn().mockResolvedValue({ kind: 'loaded', items: [] })}
        fetchStaleImpl={fetchStaleImpl}
        dismissStaleImpl={dismissStaleImpl}
        signInImpl={vi
          .fn()
          .mockResolvedValue({ kind: 'signed-in', accessToken: 'tok', refreshToken: 'r' })}
      />,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'r@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.submit(screen.getByTestId('sign-in-form'));
  }

  it('opens on the queue and counts the flags beside the other tab', async () => {
    renderReview(vi.fn().mockResolvedValue({ kind: 'loaded', items: [CHANGED, GONE] }));

    const queueTab = await screen.findByTestId('review-tab-queue');
    expect(queueTab.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('review-tab-stale').textContent).toContain('2');
    expect(screen.queryByTestId('stale-list')).toBeNull();
  });

  it('removes a dismissed flag, and it stays gone across tabs', async () => {
    const dismissStaleImpl = vi.fn().mockResolvedValue(undefined);
    renderReview(
      vi.fn().mockResolvedValue({ kind: 'loaded', items: [CHANGED, GONE] }),
      dismissStaleImpl,
    );

    fireEvent.click(await screen.findByTestId('review-tab-stale'));
    const card = screen.getByTestId('stale-flag-1');
    fireEvent.change(within(card).getByLabelText('Your note (required for either decision)'), {
      target: { value: 'typo fix' },
    });
    fireEvent.click(within(card).getByRole('button', { name: 'Dismiss' }));

    await waitFor(() => {
      expect(screen.queryByTestId('stale-flag-1')).toBeNull();
    });
    expect(dismissStaleImpl).toHaveBeenCalledWith({ token: 'tok', id: 'flag-1', note: 'typo fix' });
    expect(screen.getByTestId('review-tab-stale').textContent).toContain('1');

    fireEvent.click(screen.getByTestId('review-tab-queue'));
    fireEvent.click(screen.getByTestId('review-tab-stale'));
    expect(screen.queryByTestId('stale-flag-1')).toBeNull();
    expect(screen.getByTestId('stale-flag-2')).toBeTruthy();
  });

  it('removes a retracted flag too', async () => {
    const retractStaleImpl = vi.fn().mockResolvedValue(undefined);
    renderApp(
      <ReviewScreen
        fetchQueueImpl={vi.fn().mockResolvedValue({ kind: 'loaded', items: [] })}
        fetchStaleImpl={vi.fn().mockResolvedValue({ kind: 'loaded', items: [CHANGED, GONE] })}
        retractStaleImpl={retractStaleImpl}
        signInImpl={vi
          .fn()
          .mockResolvedValue({ kind: 'signed-in', accessToken: 'tok', refreshToken: 'r' })}
      />,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'r@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.submit(screen.getByTestId('sign-in-form'));

    fireEvent.click(await screen.findByTestId('review-tab-stale'));
    const card = screen.getByTestId('stale-flag-2');
    fireEvent.change(within(card).getByLabelText('Your note (required for either decision)'), {
      target: { value: 'withdrawn by ABB' },
    });
    fireEvent.click(within(card).getByRole('button', { name: 'Retract from live answers' }));
    fireEvent.click(within(card).getByRole('button', { name: 'Yes, retract now' }));

    await waitFor(() => {
      expect(screen.queryByTestId('stale-flag-2')).toBeNull();
    });
    expect(retractStaleImpl).toHaveBeenCalledWith({
      token: 'tok',
      id: 'flag-2',
      note: 'withdrawn by ABB',
    });
    expect(screen.getByTestId('review-tab-stale').textContent).toContain('1');
  });

  it('says when the flags could not be loaded, without losing the queue', async () => {
    renderReview(vi.fn().mockResolvedValue({ kind: 'failed' }));

    expect(await screen.findByTestId('review-tab-queue')).toBeTruthy();
    fireEvent.click(screen.getByTestId('review-tab-stale'));
    expect(screen.getByTestId('stale-failed').textContent).toMatch(/could not be loaded/);
  });
});

// --- the client ---------------------------------------------------------------

describe('the stale-document client', () => {
  it('reads the open flags with the token', async () => {
    const fetchImpl = respond(200, { items: [CHANGED] });

    expect(await fetchStale({ token: 't', fetchImpl })).toEqual({
      kind: 'loaded',
      items: [CHANGED],
    });
    expect(fetchImpl).toHaveBeenCalledWith('/api/v1/verification/stale-documents?status=open', {
      headers: { Authorization: 'Bearer t' },
    });
  });

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [500, 'failed'],
  ])('maps %i to %s', async (status, kind) => {
    expect(await fetchStale({ token: 't', fetchImpl: respond(status) })).toEqual({ kind });
  });

  it('posts the note to the flag', async () => {
    const fetchImpl = respond(200, CHANGED);
    await dismissStale({ token: 't', id: 'flag 1', note: 'typo', fetchImpl });

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/verification/stale-documents/flag%201/dismiss',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t' },
        body: JSON.stringify({ note: 'typo' }),
      },
    );
  });

  it('posts a retraction to its own endpoint', async () => {
    const fetchImpl = respond(200, CHANGED);
    await retractStale({ token: 't', id: 'flag-1', note: 'withdrawn', fetchImpl });

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/verification/stale-documents/flag-1/retract',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ note: 'withdrawn' }) }),
    );
  });

  it('carries the server’s reason when a dismissal is refused', async () => {
    const fetchImpl = respond(422, { detail: 'a dismissal needs a note' });
    await expect(dismissStale({ token: 't', id: 'f', note: '', fetchImpl })).rejects.toThrow(
      'a dismissal needs a note',
    );
  });
});
