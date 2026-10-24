import { act, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AccountSessionProvider, useAccountSession } from '@/components/account-session';
import {
  browserRefreshCoordination,
  createRefreshCoordinator,
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

    const answers = tabs.map((tab) => tab.refresh());
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

    expect(await one.refresh()).toEqual({ kind: 'refreshed', accessToken: 'first' });
    clock = 5000; // an hour, in effect: the access token has lapsed again
    expect(await two.refresh()).toEqual({ kind: 'refreshed', accessToken: 'second' });
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
    expect(await Promise.all(tabs.map((tab) => tab.refresh()))).toEqual([
      { kind: 'failed' },
      { kind: 'refreshed', accessToken: 'later' },
    ]);
  });

  it('falls back to refreshing directly where Web Locks are missing', async () => {
    const refresh = vi
      .fn<() => Promise<RefreshOutcome>>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: 't' });
    const tab = createRefreshCoordinator(refresh, {});
    await Promise.all([tab.refresh(), tab.refresh()]);
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
    await other.refresh();
    await closed.refresh();
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
          initial={{ status: 'signed-in', tokens: { accessToken: 'old' } }}
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
      gate.resolve({ kind: 'refreshed', accessToken: 'new' });
      outcomes = await pending;
    });
    expect(outcomes).toEqual(['refreshed', 'refreshed']);
    expect(refreshImpl).toHaveBeenCalledTimes(1);
    expect(held.map((tab) => tab?.state)).toEqual([
      { status: 'signed-in', tokens: { accessToken: 'new' } },
      { status: 'signed-in', tokens: { accessToken: 'new' } },
    ]);
  });
});
