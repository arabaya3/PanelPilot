import { act, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  AccountSessionProvider,
  useAccountSession,
  type AccountState,
} from '@/components/account-session';
import {
  CSRF_HEADER,
  REFRESH_TIMEOUT_MS,
  refreshSession,
  signIn,
  signOutEverywhere,
  signOutOfServer,
} from '@/lib/auth';

/**
 * The session behind a signed-in account: the refresh token is an HttpOnly
 * cookie this code never sees, the access token lives in memory, a reload
 * restores the account from the cookie, and signing out ends the session on
 * the server.
 */

/** A JWT-shaped token naming a subject, unsigned: the page only reads it. */
function tokenFor(subject: string, n = 1): string {
  const payload = btoa(JSON.stringify({ sub: subject, tid: 'tenant', n }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `h.${payload}.s`;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('the session endpoints', () => {
  it('refreshes with the cookie and the cross-site header, never a token in the body', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json(200, { access_token: 'a2' }));
    expect(await refreshSession({ fetchImpl })).toEqual({ kind: 'refreshed', accessToken: 'a2' });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/auth/session/refresh');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('same-origin');
    expect(new Headers(init?.headers).get(CSRF_HEADER)).toBe('1');
    expect(init?.body).toBeUndefined();
  });

  it('reads a 401 as no session, and anything else as a failure', async () => {
    const answer = (response: Response | Error) =>
      refreshSession({
        fetchImpl: () =>
          response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
      });
    expect(await answer(new Response(null, { status: 401 }))).toEqual({ kind: 'expired' });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    expect(await answer(new Response(null, { status: 403 }))).toEqual({ kind: 'failed' });
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
    expect(await answer(new Response(null, { status: 500 }))).toEqual({ kind: 'failed' });
    expect(await answer(json(200, { nothing: true }))).toEqual({ kind: 'failed' });
    expect(await answer(new Error('offline'))).toEqual({ kind: 'failed' });
  });

  it('keeps no refresh token from a sign-in: the cookie holds it', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(json(200, { access_token: 'a', refresh_token: 'should-be-ignored' }));
    expect(await signIn({ email: 'e@example.com', password: 'p', fetchImpl })).toEqual({
      kind: 'signed-in',
      accessToken: 'a',
    });
    expect(fetchImpl.mock.calls[0]?.[1]?.credentials).toBe('same-origin');
  });

  it('signs out with the access token as the bearer and the cross-site header', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await signOutOfServer({ accessToken: 'a', fetchImpl })).toEqual({ kind: 'signed-out' });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/auth/session/logout');
    const headers = new Headers(init?.headers);
    expect(headers.get('Authorization')).toBe('Bearer a');
    expect(headers.get(CSRF_HEADER)).toBe('1');
  });

  it('signs out with the cookie alone when no access token is in hand', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    await signOutOfServer({ accessToken: null, fetchImpl });
    expect(new Headers(fetchImpl.mock.calls[0]?.[1]?.headers).get('Authorization')).toBeNull();
  });

  it('signs out everywhere at its own endpoint, and reports a refusal as a failure', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await signOutEverywhere({ accessToken: 'a', fetchImpl })).toEqual({
      kind: 'signed-out',
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe('/api/v1/auth/session/logout-everywhere');
    const refused = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 401 }));
    expect(await signOutEverywhere({ accessToken: 'a', fetchImpl: refused })).toEqual({
      kind: 'failed',
    });
  });

  it('stores nothing in local or session storage', async () => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(json(200, { access_token: 'a' }));
    await signIn({ email: 'e@example.com', password: 'p', fetchImpl });
    await refreshSession({ fetchImpl });
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});

let held: ReturnType<typeof useAccountSession> = null;
function Probe() {
  held = useAccountSession();
  return <p data-testid="probe">{held?.state.status}</p>;
}

describe('restoring the account on load', () => {
  it('renders nothing until the cookie has answered, then starts signed in', async () => {
    let answer: (outcome: Awaited<ReturnType<typeof refreshSession>>) => void = () => undefined;
    const refreshImpl = vi.fn<typeof refreshSession>().mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    render(
      <AccountSessionProvider restore refreshImpl={refreshImpl}>
        <Probe />
      </AccountSessionProvider>,
    );
    expect(screen.getByTestId('account-restoring')).toBeTruthy();
    expect(screen.queryByTestId('probe')).toBeNull();

    await act(async () => {
      answer({ kind: 'refreshed', accessToken: 'restored' });
      await Promise.resolve();
    });
    expect(screen.getByTestId('probe').textContent).toBe('signed-in');
    expect(held?.state).toEqual({ status: 'signed-in', tokens: { accessToken: 'restored' } });
    expect(refreshImpl).toHaveBeenCalledTimes(1);
  });

  it('starts signed out when the browser has no session', async () => {
    const refreshImpl = vi.fn<typeof refreshSession>().mockResolvedValue({ kind: 'expired' });
    render(
      <AccountSessionProvider restore refreshImpl={refreshImpl}>
        <Probe />
      </AccountSessionProvider>,
    );
    expect((await screen.findByTestId('probe')).textContent).toBe('signed-out');
  });

  it('starts signed out, and says why in the log, when the server cannot be reached', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const refreshImpl = vi.fn<typeof refreshSession>().mockResolvedValue({ kind: 'failed' });
    render(
      <AccountSessionProvider restore refreshImpl={refreshImpl}>
        <Probe />
      </AccountSessionProvider>,
    );
    expect((await screen.findByTestId('probe')).textContent).toBe('signed-out');
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('does not restore unless asked, so a page rendered alone starts from its initial state', () => {
    const refreshImpl = vi.fn<typeof refreshSession>();
    render(
      <AccountSessionProvider refreshImpl={refreshImpl}>
        <Probe />
      </AccountSessionProvider>,
    );
    expect(screen.getByTestId('probe').textContent).toBe('signed-out');
    expect(refreshImpl).not.toHaveBeenCalled();
  });
});

describe('two tabs sharing the one cookie', () => {
  function renderTab(refreshImpl: typeof refreshSession, subject: string) {
    let tab: ReturnType<typeof useAccountSession> = null;
    function TabProbe() {
      tab = useAccountSession();
      return null;
    }
    render(
      <AccountSessionProvider
        initial={{ status: 'signed-in', tokens: { accessToken: tokenFor(subject) } }}
        refreshImpl={refreshImpl}
      >
        <TabProbe />
      </AccountSessionProvider>,
    );
    return () => tab;
  }

  it('both stay signed in when they renew at the same moment', async () => {
    // The server lets a refresh that races another one through (see the API's
    // reuse window), so each tab gets a token of its own.
    let n = 0;
    const refreshImpl = vi.fn<typeof refreshSession>().mockImplementation(() => {
      n += 1;
      return Promise.resolve({ kind: 'refreshed', accessToken: tokenFor('engineer', n + 1) });
    });
    const first = renderTab(refreshImpl, 'engineer');
    const second = renderTab(refreshImpl, 'engineer');
    let outcomes: string[] = [];
    await act(async () => {
      outcomes = await Promise.all([
        first()?.renew() ?? 'none',
        second()?.renew() ?? 'none',
        // A second 401 in the same tab waits for that tab's exchange.
        first()?.renew() ?? 'none',
      ]);
    });
    expect(outcomes).toEqual(['refreshed', 'refreshed', 'refreshed']);
    expect(refreshImpl).toHaveBeenCalledTimes(2);
    expect(first()?.state.status).toBe('signed-in');
    expect(second()?.state.status).toBe('signed-in');
  });

  it('does not become another account that signed in in another tab', async () => {
    const refreshImpl = vi
      .fn<typeof refreshSession>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: tokenFor('someone-else') });
    const tab = renderTab(refreshImpl, 'engineer');
    let outcome = '';
    await act(async () => {
      outcome = (await tab()?.renew()) ?? '';
    });
    expect(outcome).toBe('expired');
    expect(tab()?.state).toEqual({ status: 'expired' });
  });

  it('asks for the account again once another tab has signed out', async () => {
    const refreshImpl = vi.fn<typeof refreshSession>().mockResolvedValue({ kind: 'expired' });
    const tab = renderTab(refreshImpl, 'engineer');
    await act(async () => {
      await tab()?.renew();
    });
    expect(tab()?.state).toEqual({ status: 'expired' });
  });
});

describe('the shared session signing out', () => {
  function renderWith(
    initial: AccountState,
    signOutImpl: typeof signOutOfServer,
    signOutEverywhereImpl: typeof signOutEverywhere = vi.fn(),
  ) {
    render(
      <AccountSessionProvider
        initial={initial}
        signOutImpl={signOutImpl}
        signOutEverywhereImpl={signOutEverywhereImpl}
      >
        <Probe />
      </AccountSessionProvider>,
    );
  }

  it('ends the session on the server with the token it held, then forgets it', () => {
    const signOutImpl = vi.fn<typeof signOutOfServer>().mockResolvedValue({ kind: 'signed-out' });
    renderWith({ status: 'signed-in', tokens: { accessToken: 'acc' } }, signOutImpl);
    act(() => {
      held?.signOut();
    });
    expect(signOutImpl).toHaveBeenCalledWith({ accessToken: 'acc' });
    expect(held?.state).toEqual({ status: 'signed-out' });
  });

  it('ends an expired session by its cookie', () => {
    const signOutImpl = vi.fn<typeof signOutOfServer>().mockResolvedValue({ kind: 'signed-out' });
    renderWith({ status: 'expired' }, signOutImpl);
    act(() => {
      held?.signOut();
    });
    expect(signOutImpl).toHaveBeenCalledWith({ accessToken: null });
  });

  it('is signed out on the page even when the server could not be reached', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const signOutImpl = vi.fn<typeof signOutOfServer>().mockResolvedValue({ kind: 'failed' });
    renderWith({ status: 'signed-in', tokens: { accessToken: 'acc' } }, signOutImpl);
    await act(async () => {
      held?.signOut();
      await Promise.resolve();
    });
    expect(held?.state).toEqual({ status: 'signed-out' });
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('asks nothing of the server when no account is signed in (a trial)', () => {
    const signOutImpl = vi.fn<typeof signOutOfServer>();
    renderWith({ status: 'signed-out' }, signOutImpl);
    act(() => {
      held?.signOut();
    });
    expect(signOutImpl).not.toHaveBeenCalled();
  });

  it('signs out everywhere, and only once the server has done it', async () => {
    const everywhere = vi
      .fn<typeof signOutEverywhere>()
      .mockResolvedValueOnce({ kind: 'failed' })
      .mockResolvedValueOnce({ kind: 'signed-out' });
    renderWith({ status: 'signed-in', tokens: { accessToken: 'acc' } }, vi.fn(), everywhere);

    let outcome = '';
    await act(async () => {
      outcome = (await held?.signOutEverywhere()) ?? '';
    });
    expect(outcome).toBe('failed');
    expect(held?.state.status).toBe('signed-in');

    await act(async () => {
      outcome = (await held?.signOutEverywhere()) ?? '';
    });
    expect(outcome).toBe('signed-out');
    expect(everywhere).toHaveBeenLastCalledWith({ accessToken: 'acc' });
    await waitFor(() => {
      expect(held?.state).toEqual({ status: 'signed-out' });
    });
  });
});

describe('a restore that does not answer', () => {
  it('gives up after the timeout instead of waiting forever', async () => {
    // A fetch that only ever ends when its signal aborts it, like a hung API.
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(init.signal?.reason as Error);
          });
        }),
    );
    expect(await refreshSession({ fetchImpl, timeoutMs: 20 })).toEqual({ kind: 'timed-out' });
    expect(fetchImpl.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
  });

  it('waits eight seconds by default', () => {
    expect(REFRESH_TIMEOUT_MS).toBe(8000);
  });

  it('asks for the account, with a notice, rather than starting as signed out', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const refreshImpl = vi.fn<typeof refreshSession>().mockResolvedValue({ kind: 'timed-out' });
    render(
      <AccountSessionProvider restore refreshImpl={refreshImpl}>
        <Probe />
      </AccountSessionProvider>,
    );
    // `expired`, not `signed-out`: no page starts a trial over what may be a
    // signed-in identity.
    expect((await screen.findByTestId('probe')).textContent).toBe('expired');
    expect(held?.restoreTimedOut).toBe(true);
    warn.mockRestore();
  });
});
