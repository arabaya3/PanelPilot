import { act, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  AccountSessionProvider,
  useAccountSession,
  type AccountState,
} from '@/components/account-session';
import { signOutOfServer } from '@/lib/auth';

/** Signing out ends the session on the server too, then on the page whatever it answers. */

describe('signOutOfServer', () => {
  it('posts the refresh token with the access token as the bearer', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    expect(await signOutOfServer({ accessToken: 'a', refreshToken: 'r', fetchImpl })).toEqual({
      kind: 'signed-out',
    });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/auth/logout');
    expect(init?.method).toBe('POST');
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer a');
    expect(JSON.parse(init?.body as string)).toEqual({ refresh_token: 'r' });
  });

  it('takes a 401 as already signed out, and anything else as a failure', async () => {
    const answer = (response: Response | Error) =>
      signOutOfServer({
        accessToken: 'a',
        refreshToken: 'r',
        fetchImpl: () =>
          response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
      });
    expect(await answer(new Response(null, { status: 401 }))).toEqual({ kind: 'signed-out' });
    expect(await answer(new Response(null, { status: 500 }))).toEqual({ kind: 'failed' });
    expect(await answer(new Error('offline'))).toEqual({ kind: 'failed' });
  });
});

describe('the shared session signing out', () => {
  let held: ReturnType<typeof useAccountSession> = null;
  function Probe() {
    held = useAccountSession();
    return null;
  }
  function renderWith(initial: AccountState, signOutImpl: typeof signOutOfServer) {
    render(
      <AccountSessionProvider initial={initial} signOutImpl={signOutImpl}>
        <Probe />
      </AccountSessionProvider>,
    );
  }

  it('ends the session on the server with the tokens it held, then forgets them', () => {
    const signOutImpl = vi.fn<typeof signOutOfServer>().mockResolvedValue({ kind: 'signed-out' });
    renderWith(
      { status: 'signed-in', tokens: { accessToken: 'acc', refreshToken: 'ref' } },
      signOutImpl,
    );
    act(() => {
      held?.signOut();
    });
    expect(signOutImpl).toHaveBeenCalledWith({ accessToken: 'acc', refreshToken: 'ref' });
    expect(held?.state).toEqual({ status: 'signed-out' });
  });

  it('is signed out on the page even when the server could not be reached', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const signOutImpl = vi.fn<typeof signOutOfServer>().mockResolvedValue({ kind: 'failed' });
    renderWith(
      { status: 'signed-in', tokens: { accessToken: 'acc', refreshToken: 'ref' } },
      signOutImpl,
    );
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
});
