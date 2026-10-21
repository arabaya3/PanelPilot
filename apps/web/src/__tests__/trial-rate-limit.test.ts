import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { acquireTrial } from '@/lib/session';
import { readTrial, resumeTrial, storeTrial, type TrialSession } from '@/lib/trial';

/**
 * A 429 on resume is the network's trial allowance running out, not a broken
 * server: it must read as rate-limited, and the stored trial must be kept so
 * the conversation is still reachable once the window clears.
 */

const TRIAL: TrialSession = { sessionId: 'sess-1', claimSecret: 'secret-1' };

function respond(status: number) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve({}),
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('resuming a trial while rate limited', () => {
  it('says so rather than reporting a failure', async () => {
    expect(await resumeTrial(TRIAL, { fetchImpl: respond(429) })).toEqual({
      kind: 'rate-limited',
    });
  });

  it('keeps the stored trial and does not start another', async () => {
    storeTrial(TRIAL);
    const fetchImpl = respond(429);
    vi.stubGlobal('fetch', fetchImpl);

    expect(await acquireTrial()).toEqual({ kind: 'rate-limited' });
    expect(readTrial()).toEqual(TRIAL);
    // Only the resume was attempted; a new trial would be refused as well.
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
