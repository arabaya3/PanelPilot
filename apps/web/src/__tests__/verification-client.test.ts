import { describe, expect, it, vi } from 'vitest';

import { ClaimConflict } from '@/components/verification';
import { loadQueue, signIn, tokenIsReviewer, verificationApi } from '@/lib/verification';

function respond(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

/** A JWT-shaped token carrying the given roles; the signature is not checked here. */
function tokenWith(roles: string[]): string {
  const encode = (value: unknown) => btoa(JSON.stringify(value)).replace(/=+$/, '');
  return `${encode({ alg: 'HS256' })}.${encode({ sub: 'u', roles })}.sig`;
}

describe('signIn', () => {
  it('returns the access token', async () => {
    const outcome = await signIn('a@b.c', 'pw', respond(200, { access_token: 'tok' }));
    expect(outcome).toEqual({ kind: 'signed-in', token: 'tok' });
  });

  it('tells rejected credentials apart from a broken service', async () => {
    expect(await signIn('a@b.c', 'pw', respond(401, {}))).toEqual({ kind: 'rejected' });
    expect(await signIn('a@b.c', 'pw', respond(500, {}))).toEqual({ kind: 'failed' });
    expect(
      await signIn('a@b.c', 'pw', vi.fn().mockRejectedValue(new TypeError('offline'))),
    ).toEqual({ kind: 'failed' });
  });
});

describe('tokenIsReviewer', () => {
  it('reads the reviewer role from the token', () => {
    expect(tokenIsReviewer(tokenWith(['engineer', 'reviewer']))).toBe(true);
    expect(tokenIsReviewer(tokenWith(['engineer']))).toBe(false);
  });

  it('treats a malformed token as not a reviewer', () => {
    expect(tokenIsReviewer('not-a-jwt')).toBe(false);
    expect(tokenIsReviewer('a.%%%.c')).toBe(false);
  });
});

describe('loadQueue', () => {
  it('returns the items with their text', async () => {
    const outcome = await loadQueue(
      't',
      respond(200, {
        items: [
          { id: 'i1', chunk_id: 'c1', status: 'pending', assigned_at: null, content: 'F0001' },
          { nonsense: true },
        ],
      }),
    );
    expect(outcome).toEqual({
      kind: 'loaded',
      items: [{ id: 'i1', chunk_id: 'c1', status: 'pending', assigned_at: null, content: 'F0001' }],
    });
  });

  it('reports an account that cannot review', async () => {
    expect(await loadQueue('t', respond(403, {}))).toEqual({ kind: 'forbidden' });
  });
});

describe('verificationApi', () => {
  it('posts the label and note for the item', async () => {
    const fetchImpl = respond(200, {});
    await verificationApi('tok', fetchImpl).submitLabel('i/1', 'correct', 'matches p.12');

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/verification/items/i%2F1/label');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    expect(JSON.parse(init.body as string)).toEqual({ label: 'correct', note: 'matches p.12' });
  });

  it('treats someone else’s item as a claim conflict', async () => {
    await expect(
      verificationApi('tok', respond(403, {})).submitLabel('i1', 'correct', ''),
    ).rejects.toBeInstanceOf(ClaimConflict);
  });

  it('surfaces a refused promotion as a failure, not a success', async () => {
    await expect(
      verificationApi('tok', respond(409, {})).submitLabel('i1', 'correct', ''),
    ).rejects.toThrow('409');
  });
});
