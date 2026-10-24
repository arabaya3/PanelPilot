import { act, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  AccountSessionProvider,
  acceptsShared,
  useAccountSession,
} from '@/components/account-session';
import {
  browserRefreshCoordination,
  createRefreshCoordinator,
  refreshSession,
  SHARED_CLOCK_SKEW_MS,
  type RefreshChannel,
  type RefreshLocks,
  type RefreshOutcome,
} from '@/lib/auth';

/**
 * One refresh in flight per browser: tabs queue on a Web Lock, and the tab
 * that refreshed shares its access token over a BroadcastChannel, so a tab
 * that waited does not present the cookie again.
 */

/** A browser-wide lock: callbacks for one name run one at a time, in order. */
function fakeLocks(): RefreshLocks {
  let tail: Promise<unknown> = Promise.resolve();
  return {
    request<T>(_name: string, callback: () => Promise<T>): Promise<T> {
      const run = tail.then(callback, callback);
      tail = run.catch(() => undefined);
      return run;
    },
  };
}

/** Channels that deliver to every other channel of the same bus, like tabs. */
function fakeBus() {
  const members = new Set<{ listeners: Set<(event: MessageEvent) => void> }>();
  return (): RefreshChannel => {
    const me = { listeners: new Set<(event: MessageEvent) => void>() };
    members.add(me);
    return {
      postMessage(message: unknown) {
        for (const other of members) {
          if (other === me) continue;
          for (const listener of other.listeners) {
            listener(new MessageEvent('message', { data: message }));
          }
        }
      },
      addEventListener(_type, listener) {
        me.listeners.add(listener);
      },
      removeEventListener(_type, listener) {
        me.listeners.delete(listener);
      },
    };
  };
}

/** A request whose shared tokens are always acceptable, for the lock tests. */
const ANY = { accessToken: null, accept: () => true };

function deferred() {
  let resolve: (outcome: RefreshOutcome) => void = () => undefined;
  const promise = new Promise<RefreshOutcome>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('refreshing across tabs', () => {
  it('runs one refresh, and the tabs that waited take the token it shared', async () => {
    const locks = fakeLocks();
    const channel = fakeBus();
    let clock = 1000;
    const now = () => clock;
    const first = deferred();
    const refresh = vi
      .fn<() => Promise<RefreshOutcome>>()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue({ kind: 'refreshed', accessToken: 'unexpected' });
    const tabs = [1, 2, 3].map(() =>
      createRefreshCoordinator(refresh, { locks, channel: channel(), now }),
    );

    const answers = tabs.map((tab) => tab.refresh(ANY));
    clock = 1500;
    first.resolve({ kind: 'refreshed', accessToken: 'shared' });

    expect(await Promise.all(answers)).toEqual([
      { kind: 'refreshed', accessToken: 'shared' },
      { kind: 'refreshed', accessToken: 'shared' },
      { kind: 'refreshed', accessToken: 'shared' },
    ]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('refreshes afresh, with the current cookie, when the shared token is older than the ask', async () => {
    const locks = fakeLocks();
    const channel = fakeBus();
    let clock = 1000;
    const refresh = vi
      .fn<() => Promise<RefreshOutcome>>()
      .mockResolvedValueOnce({ kind: 'refreshed', accessToken: 'first' })
      .mockResolvedValueOnce({ kind: 'refreshed', accessToken: 'second' });
    const one = createRefreshCoordinator(refresh, { locks, channel: channel(), now: () => clock });
    const two = createRefreshCoordinator(refresh, { locks, channel: channel(), now: () => clock });

    expect(await one.refresh(ANY)).toEqual({ kind: 'refreshed', accessToken: 'first' });
    clock = 5000; // an hour, in effect: the access token has lapsed again
    expect(await two.refresh(ANY)).toEqual({ kind: 'refreshed', accessToken: 'second' });
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('does not share a failure: the waiting tab tries for itself', async () => {
    const locks = fakeLocks();
    const channel = fakeBus();
    const refresh = vi
      .fn<() => Promise<RefreshOutcome>>()
      .mockResolvedValueOnce({ kind: 'failed' })
      .mockResolvedValueOnce({ kind: 'refreshed', accessToken: 'later' });
    const tabs = [1, 2].map(() =>
      createRefreshCoordinator(refresh, { locks, channel: channel(), now: () => 1 }),
    );
    expect(await Promise.all(tabs.map((tab) => tab.refresh(ANY)))).toEqual([
      { kind: 'failed' },
      { kind: 'refreshed', accessToken: 'later' },
    ]);
  });

  it('falls back to refreshing directly where Web Locks are missing', async () => {
    const refresh = vi
      .fn<() => Promise<RefreshOutcome>>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: 't' });
    const tab = createRefreshCoordinator(refresh, {});
    await Promise.all([tab.refresh(ANY), tab.refresh(ANY)]);
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('uses no channel in a browser without Web Locks (as this test DOM is)', () => {
    expect(browserRefreshCoordination()).toEqual({});
  });

  it('stops listening once closed', async () => {
    const locks = fakeLocks();
    const channel = fakeBus();
    const refresh = vi
      .fn<() => Promise<RefreshOutcome>>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: 't' });
    const closed = createRefreshCoordinator(refresh, { locks, channel: channel(), now: () => 1 });
    closed.close();
    const other = createRefreshCoordinator(refresh, { locks, channel: channel(), now: () => 2 });
    await other.refresh(ANY);
    await closed.refresh(ANY);
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});

describe('two signed-in tabs under the coordinator', () => {
  it('renew together with one refresh between them', async () => {
    const locks = fakeLocks();
    const channel = fakeBus();
    const gate = deferred();
    const refreshImpl = vi.fn().mockReturnValueOnce(gate.promise);
    const held: (ReturnType<typeof useAccountSession> | null)[] = [null, null];
    function Probe({ index }: { index: number }) {
      held[index] = useAccountSession();
      return null;
    }
    for (const index of [0, 1]) {
      render(
        <AccountSessionProvider
          initial={{ status: 'signed-in', tokens: { accessToken: jwt(LIVE) } }}
          refreshImpl={refreshImpl}
          coordination={{ locks, channel: channel(), now: () => 1 }}
        >
          <Probe index={index} />
        </AccountSessionProvider>,
      );
    }
    let outcomes: string[] = [];
    await act(async () => {
      const pending = Promise.all(held.map((tab) => tab?.renew() ?? Promise.resolve('none')));
      gate.resolve({ kind: 'refreshed', accessToken: jwt({ ...LIVE, n: 2 }) });
      outcomes = await pending;
    });
    expect(outcomes).toEqual(['refreshed', 'refreshed']);
    expect(refreshImpl).toHaveBeenCalledTimes(1);
    expect(held.map((tab) => tab?.state)).toEqual([
      { status: 'signed-in', tokens: { accessToken: jwt({ ...LIVE, n: 2 }) } },
      { status: 'signed-in', tokens: { accessToken: jwt({ ...LIVE, n: 2 }) } },
    ]);
  });
});

/** A JWT-shaped, unsigned token: the page only reads it. */
function jwt(claims: Record<string, unknown>): string {
  const body = btoa(JSON.stringify(claims))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `h.${body}.s`;
}

const NOW = 1_800_000_000_000;
const LIVE = { sub: 'user-1', tid: 't', sid: 'sess-1', exp: NOW / 1000 + 600 };

describe('what a tab accepts from another', () => {
  const held = jwt(LIVE);

  it('takes a token of the same account and session that has not expired', () => {
    expect(acceptsShared(held, () => NOW)(jwt({ ...LIVE, n: 2 }))).toBe(true);
  });

  it('refuses another account, another session, an expired token, a trial or junk', () => {
    const accept = acceptsShared(held, () => NOW);
    expect(accept(jwt({ ...LIVE, sub: 'user-2' }))).toBe(false);
    expect(accept(jwt({ ...LIVE, sid: 'sess-2' }))).toBe(false);
    expect(accept(jwt({ ...LIVE, exp: NOW / 1000 - 1 }))).toBe(false);
    expect(accept(jwt({ sub: 'anon', tid: 't', exp: NOW / 1000 + 600 }))).toBe(false);
    expect(accept('not-a-token')).toBe(false);
  });

  it('on load, takes any unexpired account token, and nothing else', () => {
    const accept = acceptsShared(null, () => NOW);
    expect(accept(jwt({ ...LIVE, sub: 'whoever' }))).toBe(true);
    expect(accept(jwt({ ...LIVE, exp: NOW / 1000 - 1 }))).toBe(false);
    expect(accept(jwt({ sub: 'anon', tid: 't', exp: NOW / 1000 + 600 }))).toBe(false);
  });
});

describe('the shared token is checked before it is adopted', () => {
  it('refreshes itself, with its own bearer, when the shared token is not acceptable', async () => {
    const locks = fakeLocks();
    const channel = fakeBus();
    const first = deferred();
    const refresh = vi
      .fn<(accessToken: string | null) => Promise<RefreshOutcome>>()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({ kind: 'refreshed', accessToken: 'own' });
    const one = createRefreshCoordinator(refresh, { locks, channel: channel(), now: () => 1 });
    const two = createRefreshCoordinator(refresh, { locks, channel: channel(), now: () => 1 });

    const answers = [
      one.refresh({ accessToken: 'mine-1', accept: () => true }),
      two.refresh({ accessToken: 'mine-2', accept: () => false }),
    ];
    first.resolve({ kind: 'refreshed', accessToken: 'shared' });
    expect(await Promise.all(answers)).toEqual([
      { kind: 'refreshed', accessToken: 'shared' },
      { kind: 'refreshed', accessToken: 'own' },
    ]);
    expect(refresh.mock.calls).toEqual([['mine-1'], ['mine-2']]);
  });

  it('ignores a shared timestamp beyond the allowed skew', async () => {
    const locks = fakeLocks();
    const bus = fakeBus();
    const sender = bus();
    const refresh = vi
      .fn<(accessToken: string | null) => Promise<RefreshOutcome>>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: 'own' });
    const tab = createRefreshCoordinator(refresh, { locks, channel: bus(), now: () => 1000 });

    sender.postMessage({
      kind: 'pp-refreshed',
      accessToken: 'planted',
      at: 1000 + SHARED_CLOCK_SKEW_MS + 1,
    });
    expect(await tab.refresh(ANY)).toEqual({ kind: 'refreshed', accessToken: 'own' });

    sender.postMessage({ kind: 'pp-refreshed', accessToken: 'fresh', at: 1001 });
    expect(await tab.refresh(ANY)).toEqual({ kind: 'refreshed', accessToken: 'fresh' });
  });

  it('forgets the shared token on sign-out', async () => {
    const locks = fakeLocks();
    const bus = fakeBus();
    const sender = bus();
    const refresh = vi
      .fn<(accessToken: string | null) => Promise<RefreshOutcome>>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: 'own' });
    const tab = createRefreshCoordinator(refresh, { locks, channel: bus(), now: () => 1000 });
    sender.postMessage({ kind: 'pp-refreshed', accessToken: 'shared', at: 1000 });
    tab.forget();
    expect(await tab.refresh(ANY)).toEqual({ kind: 'refreshed', accessToken: 'own' });
  });
});

describe('the refresh call itself', () => {
  it('sends the access token in hand as the bearer, and none on load', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ access_token: 'a' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await refreshSession({ fetchImpl, accessToken: 'held' });
    expect(new Headers(fetchImpl.mock.calls[0]?.[1]?.headers).get('Authorization')).toBe(
      'Bearer held',
    );
    await refreshSession({ fetchImpl });
    expect(new Headers(fetchImpl.mock.calls[1]?.[1]?.headers).get('Authorization')).toBeNull();
  });

  it('is what a signed-in tab renews with: its own token goes along', async () => {
    const held = jwt(LIVE);
    const refreshImpl = vi
      .fn<typeof refreshSession>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: jwt({ ...LIVE, n: 3 }) });
    let session: ReturnType<typeof useAccountSession> = null;
    function Probe() {
      session = useAccountSession();
      return null;
    }
    render(
      <AccountSessionProvider
        initial={{ status: 'signed-in', tokens: { accessToken: held } }}
        refreshImpl={refreshImpl}
      >
        <Probe />
      </AccountSessionProvider>,
    );
    await act(async () => {
      await session?.renew();
    });
    expect(refreshImpl).toHaveBeenCalledWith({ accessToken: held });
  });
});
