import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ReviewScreen } from '@/components/review-screen';
import { SignInForm } from '@/components/sign-in-form';
import { ClaimConflict } from '@/components/verification';
import { signIn } from '@/lib/auth';
import { fetchQueue, sourceUrlFor, submitLabel } from '@/lib/verification';

import { renderApp } from './helpers';

/**
 * Signing in, and the reviewer's page.
 *
 * Neither existed: an account could not come back once its tab closed, and
 * the verification console had no page, so nobody could label anything.
 */

function respond(status: number, body: unknown = {}): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

function calls(fetchImpl: typeof fetch) {
  return (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls as [
    string,
    { method?: string; body?: string; headers?: Record<string, string> },
  ][];
}

const ITEM = {
  id: 'item-1',
  chunk_id: 'doc#0001', // allow-hardcoded-colour (a chunk id, not a colour)
  status: 'pending',
  assigned_at: '2026-09-29T00:00:00Z',
  content: 'F0001 OVERCURRENT: check the motor cable insulation.',
  source_url: 'https://library.abb.com/acs880.pdf',
  page: 88,
  section: 'Fault tracing',
};

function fillSignIn(email = 'r@example.com', password = 'pw') {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: email } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
  fireEvent.submit(screen.getByTestId('sign-in-form'));
}

// --- signIn ---------------------------------------------------------------------

describe('signIn', () => {
  it('posts the credentials and returns both tokens', async () => {
    const fetchImpl = respond(200, { access_token: 'a', refresh_token: 'r' });

    expect(await signIn({ email: 'e@x.com', password: 'pw', fetchImpl })).toEqual({
      kind: 'signed-in',
      accessToken: 'a',
      refreshToken: 'r',
    });
    const [url, init] = calls(fetchImpl)[0] ?? ['', {}];
    expect(url).toBe('/api/v1/auth/login');
    expect(JSON.parse(init.body ?? '{}')).toEqual({ email: 'e@x.com', password: 'pw' });
  });

  it.each([
    [401, 'invalid'],
    [422, 'invalid'],
    [429, 'rate-limited'],
    [500, 'failed'],
  ] as const)('reads %i as %s', async (status, kind) => {
    expect(await signIn({ email: 'e', password: 'p', fetchImpl: respond(status) })).toEqual({
      kind,
    });
  });

  it('reads a response without tokens as a failure', async () => {
    expect(await signIn({ email: 'e', password: 'p', fetchImpl: respond(200, {}) })).toEqual({
      kind: 'failed',
    });
  });
});

describe('the sign-in form', () => {
  it('hands the tokens over on success', async () => {
    const onSignedIn = vi.fn();
    const signInImpl = vi
      .fn()
      .mockResolvedValue({ kind: 'signed-in', accessToken: 'a', refreshToken: 'r' });
    renderApp(<SignInForm onSignedIn={onSignedIn} signInImpl={signInImpl} />);

    fillSignIn(' r@example.com ');

    await waitFor(() => {
      expect(onSignedIn).toHaveBeenCalledWith({ accessToken: 'a', refreshToken: 'r' });
    });
    expect(signInImpl).toHaveBeenCalledWith({ email: 'r@example.com', password: 'pw' });
  });

  it('names the reason it failed', async () => {
    renderApp(
      <SignInForm
        onSignedIn={vi.fn()}
        signInImpl={vi.fn().mockResolvedValue({ kind: 'invalid' })}
      />,
    );

    fillSignIn();

    expect((await screen.findByTestId('sign-in-error')).textContent).toMatch(/do not match/);
  });
});

// --- the review page -------------------------------------------------------------

describe('the review page', () => {
  function renderReview(
    fetchQueueImpl: typeof fetchQueue,
    submitLabelImpl: typeof submitLabel = vi.fn().mockResolvedValue(undefined),
  ) {
    const signInImpl = vi
      .fn()
      .mockResolvedValue({ kind: 'signed-in', accessToken: 'tok', refreshToken: 'r' });
    renderApp(
      <ReviewScreen
        fetchQueueImpl={fetchQueueImpl}
        submitLabelImpl={submitLabelImpl}
        signInImpl={signInImpl}
      />,
    );
    fillSignIn();
  }

  it('asks a signed-out visitor to sign in, and loads nothing', () => {
    const fetchQueueImpl = vi.fn();
    renderApp(<ReviewScreen fetchQueueImpl={fetchQueueImpl} />);

    expect(screen.getByTestId('sign-in-form')).toBeTruthy();
    expect(fetchQueueImpl).not.toHaveBeenCalled();
  });

  it('shows the text under review and its source', async () => {
    const fetchQueueImpl = vi.fn().mockResolvedValue({ kind: 'loaded', items: [ITEM] });
    renderReview(fetchQueueImpl);

    expect((await screen.findByTestId('proposed-content')).textContent).toBe(ITEM.content);
    // The templated labels are filled, not printed as their message keys.
    expect(screen.getByTestId('remaining').textContent).toBe('1 remaining');
    expect(screen.getByTestId('chunk-id').textContent).toContain('p. 88');
    expect(fetchQueueImpl).toHaveBeenCalledWith({ token: 'tok' });
    expect(screen.getByTestId('source-frame').getAttribute('src')).toBe(
      'https://library.abb.com/acs880.pdf#page=88',
    );
    expect(screen.getByTestId('source-link').getAttribute('target')).toBe('_blank');
  });

  it('labels with the signed-in token', async () => {
    const submitLabelImpl = vi.fn().mockResolvedValue(undefined);
    renderReview(vi.fn().mockResolvedValue({ kind: 'loaded', items: [ITEM] }), submitLabelImpl);

    fireEvent.click(await screen.findByTestId('label-correct'));
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));

    await waitFor(() => {
      expect(submitLabelImpl).toHaveBeenCalledWith({
        token: 'tok',
        itemId: 'item-1',
        label: 'correct',
        note: '',
      });
    });
  });

  it('tells an account without the reviewer role so', async () => {
    renderReview(vi.fn().mockResolvedValue({ kind: 'forbidden' }));

    expect((await screen.findByTestId('review-forbidden')).textContent).toMatch(/reviewer role/);
  });

  it('says when an item’s text could not be loaded', async () => {
    renderReview(
      vi.fn().mockResolvedValue({ kind: 'loaded', items: [{ ...ITEM, content: null }] }),
    );

    expect(await screen.findByTestId('content-missing')).toBeTruthy();
    expect(screen.queryByTestId('proposed-content')).toBeNull();
  });
});

// --- the queue client -----------------------------------------------------------

describe('the queue client', () => {
  it('reads the queue with the token', async () => {
    const fetchImpl = respond(200, { items: [ITEM] });

    expect(await fetchQueue({ token: 't', fetchImpl })).toEqual({ kind: 'loaded', items: [ITEM] });
    const [url, init] = calls(fetchImpl)[0] ?? ['', {}];
    expect(url).toBe('/api/v1/verification/queue/me');
    expect(init.headers).toEqual({ Authorization: 'Bearer t' });
  });

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [500, 'failed'],
  ] as const)('reads %i as %s', async (status, kind) => {
    expect(await fetchQueue({ token: 't', fetchImpl: respond(status) })).toEqual({ kind });
  });

  it('posts a label to the item', async () => {
    const fetchImpl = respond(200, {});
    await submitLabel({ token: 't', itemId: 'a/b', label: 'incorrect', note: 'wrong', fetchImpl });

    const [url, init] = calls(fetchImpl)[0] ?? ['', {}];
    expect(url).toBe('/api/v1/verification/items/a%2Fb/label');
    expect(JSON.parse(init.body ?? '{}')).toEqual({ label: 'incorrect', note: 'wrong' });
  });

  it('reports an item that is no longer this reviewer’s as a claim conflict', async () => {
    await expect(
      submitLabel({ token: 't', itemId: 'a', label: 'correct', note: '', fetchImpl: respond(403) }),
    ).rejects.toBeInstanceOf(ClaimConflict);
  });

  it('points the frame at the page when it is known', () => {
    expect(sourceUrlFor({ ...ITEM, page: null })).toBe('https://library.abb.com/acs880.pdf');
    expect(sourceUrlFor({ ...ITEM, source_url: null })).toBeNull();
  });
});
