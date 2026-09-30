import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SearchScreen } from '@/components/search-screen';
import { passageSourceUrl, searchDocuments } from '@/lib/search';
import { acquireTrial } from '@/lib/session';
import * as trial from '@/lib/trial';

import { renderApp } from './helpers';

/**
 * Searching the verified manuals directly.
 *
 * `POST /search` answered 501: retrieval existed and served only the chat.
 */

const PASSAGE = {
  id: 'c1',
  text: 'F0001 OVERCURRENT\nCheck the motor cable insulation.',
  score: 0.9,
  similarity: 0.7,
  anchored: true,
  citation: {
    document_id: 'https://library.abb.com/acs880.pdf',
    document_title: 'Fault tracing',
    manufacturer: 'ABB',
    page: 88,
    section: 'Fault tracing',
  },
};

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

function respond(status: number, body: unknown = {}): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

function renderSearch(
  searchImpl: typeof searchDocuments,
  acquireImpl: typeof acquireTrial = vi.fn().mockResolvedValue(READY),
) {
  renderApp(<SearchScreen acquireImpl={acquireImpl} searchImpl={searchImpl} />);
}

async function search(text: string) {
  const input = await screen.findByLabelText('What are you looking for?');
  fireEvent.change(input, { target: { value: text } });
  const button = screen.getByRole('button', { name: 'Search' });
  await waitFor(() => {
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });
  fireEvent.click(button);
}

// --- the page -----------------------------------------------------------------

describe('the search page', () => {
  it('shows each passage with its source, at its page', async () => {
    const searchImpl = vi.fn().mockResolvedValue({ kind: 'loaded', passages: [PASSAGE] });
    renderSearch(searchImpl);

    await search('  F0001  ');

    const card = await screen.findByTestId('passage-c1');
    expect(card.textContent).toContain('Check the motor cable insulation.');
    expect(card.textContent).toContain('page 88');
    expect(screen.getByTestId('passage-source-c1').getAttribute('href')).toBe(
      'https://library.abb.com/acs880.pdf#page=88',
    );
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('1 results for “F0001”');
    expect(searchImpl).toHaveBeenCalledWith({ token: 'tok', query: 'F0001', manufacturers: [] });
  });

  it('lays the manual text out in its own direction inside an RTL page', async () => {
    // In the page's RTL direction, "23.12 Acceleration time 1" rendered as
    // "Acceleration time 1 23.12": the parameter number moved.
    renderSearch(vi.fn().mockResolvedValue({ kind: 'loaded', passages: [PASSAGE] }));
    await search('F0001');

    const card = await screen.findByTestId('passage-c1');
    const text = Array.from(card.querySelectorAll('p')).find((p) =>
      p.textContent.includes('motor cable'),
    );
    expect(text?.getAttribute('dir')).toBe('auto');
  });

  it('narrows to one manufacturer when asked', async () => {
    const searchImpl = vi.fn().mockResolvedValue({ kind: 'loaded', passages: [] });
    renderSearch(searchImpl);

    fireEvent.change(await screen.findByLabelText('Manufacturer'), {
      target: { value: 'Siemens' },
    });
    await search('F30001');

    await waitFor(() => {
      expect(searchImpl).toHaveBeenCalledWith({
        token: 'tok',
        query: 'F30001',
        manufacturers: ['Siemens'],
      });
    });
    expect((await screen.findByTestId('search-empty')).textContent).toMatch(/Nothing verified/);
  });

  it('will not search nothing', async () => {
    const searchImpl = vi.fn();
    renderSearch(searchImpl);

    fireEvent.change(await screen.findByLabelText('What are you looking for?'), {
      target: { value: '   ' },
    });
    const button = screen.getByRole('button', { name: 'Search' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(button.closest('form') as HTMLFormElement);
    expect(searchImpl).not.toHaveBeenCalled();
  });

  it('says so when retrieval is down', async () => {
    renderSearch(vi.fn().mockResolvedValue({ kind: 'unavailable' }));
    await search('F0001');
    expect((await screen.findByTestId('search-error')).textContent).toMatch(/unavailable/);
  });

  it('gets a fresh session when the token lapsed', async () => {
    const acquireImpl = vi.fn().mockResolvedValue(READY);
    renderSearch(vi.fn().mockResolvedValue({ kind: 'unauthorized' }), acquireImpl);
    await search('F0001');

    expect((await screen.findByTestId('search-error')).textContent).toMatch(/expired/);
    await waitFor(() => {
      expect(acquireImpl).toHaveBeenCalledTimes(2);
    });
  });

  it('says why it cannot search when no session could start, and retries', async () => {
    const acquireImpl = vi
      .fn()
      .mockResolvedValueOnce({ kind: 'rate-limited' })
      .mockResolvedValueOnce(READY);
    renderSearch(vi.fn(), acquireImpl);

    expect((await screen.findByTestId('search-session-failed')).textContent).toMatch(
      /Too many sessions/,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => {
      expect(screen.queryByTestId('search-session-failed')).toBeNull();
    });
  });

  it('is in the main navigation', async () => {
    renderSearch(vi.fn());
    const link = await screen.findByRole('link', { name: 'Search' });
    expect(link.getAttribute('href')).toBe('/search');
  });
});

// --- the client ---------------------------------------------------------------

describe('searchDocuments', () => {
  it('posts the query, filters and corpus with the token', async () => {
    const fetchImpl = respond(200, { passages: [PASSAGE], total: 1 });

    expect(
      await searchDocuments({ token: 't', query: 'F0001', manufacturers: ['ABB'], fetchImpl }),
    ).toEqual({ kind: 'loaded', passages: [PASSAGE] });
    expect(fetchImpl).toHaveBeenCalledWith('/api/v1/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t' },
      body: JSON.stringify({
        query: 'F0001',
        corpus: 'production',
        top_k: 10,
        filters: { manufacturers: ['ABB'] },
      }),
    });
  });

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [429, 'rate-limited'],
    [503, 'unavailable'],
    [500, 'failed'],
  ])('maps %i to %s', async (status, kind) => {
    expect(await searchDocuments({ token: 't', query: 'q', fetchImpl: respond(status) })).toEqual({
      kind,
    });
  });

  it('links a passage without a page to the document itself', () => {
    expect(passageSourceUrl({ ...PASSAGE, citation: { ...PASSAGE.citation, page: null } })).toBe(
      'https://library.abb.com/acs880.pdf',
    );
  });
});

// --- the session --------------------------------------------------------------

describe('acquireTrial', () => {
  it('resumes this browser’s trial rather than starting another', async () => {
    vi.spyOn(trial, 'readTrial').mockReturnValue({ sessionId: 's', claimSecret: 'c' });
    vi.spyOn(trial, 'resumeTrial').mockResolvedValue({ ...READY, kind: 'resumed' });
    const start = vi.spyOn(trial, 'startTrial');

    expect(await acquireTrial()).toEqual(READY);
    expect(start).not.toHaveBeenCalled();
  });

  it('keeps the stored trial when the network failed, and starts one when it is gone', async () => {
    vi.spyOn(trial, 'readTrial').mockReturnValue({ sessionId: 's', claimSecret: 'c' });
    const clear = vi.spyOn(trial, 'clearTrial');
    const resume = vi.spyOn(trial, 'resumeTrial').mockResolvedValue({ kind: 'failed' });

    expect(await acquireTrial()).toEqual({ kind: 'failed' });
    expect(clear).not.toHaveBeenCalled();

    resume.mockResolvedValue({ kind: 'gone' });
    vi.spyOn(trial, 'startTrial').mockResolvedValue({ ...READY, kind: 'started' });
    expect(await acquireTrial()).toEqual(READY);
    expect(clear).toHaveBeenCalled();
  });
});
