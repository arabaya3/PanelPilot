import { describe, expect, it, vi } from 'vitest';

import { fetchSession, listSessions } from '@/lib/sessions';

/**
 * Tests for `src/lib/sessions.ts`.
 *
 * This module's job is to never hand a component something it cannot render.
 * Every field is narrowed out of the payload rather than cast, so the tests
 * that matter are the ones where the server sends something unexpected: a
 * response shape that changed, a null where a string was promised, a body that
 * is not JSON at all. A cast would make all of those pass here and fail in the
 * sidebar, where there is no way to recover.
 */

function respond(body: unknown, status = 200): typeof fetch {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

function page(overrides: Record<string, unknown> = {}) {
  return {
    sessions: [
      {
        id: 'session-1',
        title: 'ACS880 undervoltage',
        equipment_model: 'ACS880',
        turn_count: 3,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-02T00:00:00Z',
      },
    ],
    next_cursor: null,
    ...overrides,
  };
}

// --- the happy path ----------------------------------------------------------

describe('listSessions', () => {
  it('maps the payload to camel case', async () => {
    const result = await listSessions({ token: 't', fetchImpl: respond(page()) });

    expect(result).toEqual({
      kind: 'loaded',
      sessions: [
        {
          id: 'session-1',
          title: 'ACS880 undervoltage',
          equipmentModel: 'ACS880',
          turnCount: 3,
          createdAt: '2026-01-01T00:00:00Z',
          updatedAt: '2026-01-02T00:00:00Z',
        },
      ],
      nextCursor: null,
    });
  });

  it('sends the auth token', async () => {
    const impl = respond(page());
    await listSessions({ token: 'secret-token', fetchImpl: impl });

    expect(impl).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ headers: { Authorization: 'Bearer secret-token' } }),
    );
  });

  it('omits the cursor from the first request', async () => {
    // A `cursor=` with no value is not the same as no cursor, and the API
    // would have to guess which was meant.
    const impl = respond(page());
    await listSessions({ token: 't', fetchImpl: impl });

    expect(impl).toHaveBeenCalledWith('/api/v1/sessions', expect.anything());
  });

  it('passes a cursor through unmodified', async () => {
    // It is opaque: re-encoding it here would break pagination in a way that
    // only shows up on the second page.
    const impl = respond(page());
    await listSessions({ token: 't', cursor: 'a+b/c=', fetchImpl: impl });

    const [url] = (impl as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(new URL(url, 'http://x').searchParams.get('cursor')).toBe('a+b/c=');
  });
});

// --- failures the sidebar has to tell apart ----------------------------------

describe('listSessions failures', () => {
  it('reports an absent endpoint as unavailable', async () => {
    const result = await listSessions({ token: 't', fetchImpl: respond({}, 404) });

    expect(result).toEqual({ kind: 'unavailable' });
  });

  it('reports a server error as failed', async () => {
    const result = await listSessions({ token: 't', fetchImpl: respond({}, 500) });

    expect(result).toEqual({ kind: 'failed' });
  });

  it('reports a network error as failed rather than throwing', async () => {
    // A throw here would surface as an unhandled rejection inside a render.
    const impl = vi.fn().mockRejectedValue(new Error('offline'));

    await expect(listSessions({ token: 't', fetchImpl: impl })).resolves.toEqual({
      kind: 'failed',
    });
  });

  it('reports an unparseable body as failed', async () => {
    const impl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.reject(new Error('not json')),
    });

    await expect(listSessions({ token: 't', fetchImpl: impl })).resolves.toEqual({
      kind: 'failed',
    });
  });

  it('reports a payload with no session array as failed', async () => {
    const result = await listSessions({ token: 't', fetchImpl: respond({ sessions: 'nope' }) });

    expect(result).toEqual({ kind: 'failed' });
  });
});

// --- malformed rows ----------------------------------------------------------

describe('malformed rows', () => {
  it('drops a row with no id rather than the whole page', async () => {
    // A sidebar missing one entry is recoverable; an empty history reads as
    // data loss.
    const result = await listSessions({
      token: 't',
      fetchImpl: respond(page({ sessions: [{ title: 'no id here' }, page().sessions[0]] })),
    });

    expect(result.kind).toBe('loaded');
    if (result.kind !== 'loaded') return;
    expect(result.sessions).toHaveLength(1);
    expect(result.sessions[0]?.id).toBe('session-1');
  });

  it('falls back to a title rather than rendering an empty row', async () => {
    const result = await listSessions({
      token: 't',
      fetchImpl: respond(page({ sessions: [{ id: 'a', title: '' }] })),
    });

    if (result.kind !== 'loaded') throw new Error('expected loaded');
    expect(result.sessions[0]?.title).not.toBe('');
  });

  it('treats a missing equipment model as absent, not as a string', async () => {
    // `String(null)` would print "null" under the title on every row that
    // never identified a machine.
    const result = await listSessions({
      token: 't',
      fetchImpl: respond(page({ sessions: [{ id: 'a', equipment_model: null }] })),
    });

    if (result.kind !== 'loaded') throw new Error('expected loaded');
    expect(result.sessions[0]?.equipmentModel).toBeNull();
  });

  it('treats an empty next_cursor as no next page', async () => {
    // An empty string is truthy as a cursor and would request a page that
    // cannot exist, leaving a spinner that never resolves.
    const result = await listSessions({
      token: 't',
      fetchImpl: respond(page({ next_cursor: '' })),
    });

    if (result.kind !== 'loaded') throw new Error('expected loaded');
    expect(result.nextCursor).toBeNull();
  });
});

// --- opening one conversation ------------------------------------------------

describe('fetchSession', () => {
  it('returns the conversation', async () => {
    const body = { id: 'session-1', turns: [] };
    const result = await fetchSession({
      token: 't',
      sessionId: 'session-1',
      fetchImpl: respond(body),
    });

    expect(result).toEqual({ kind: 'loaded', session: body });
  });

  it('uses the same route the chat view loads from', async () => {
    // The spec asks for hydration "via the same session-fetch path used on
    // page load" — a second route could disagree about what a stored turn is.
    const impl = respond({ id: 's', turns: [] });
    await fetchSession({ token: 't', sessionId: 's', fetchImpl: impl });

    expect(impl).toHaveBeenCalledWith('/api/v1/diagnostics/s', expect.anything());
  });

  it('escapes the session id', async () => {
    // The id reaches the URL; a caller-supplied value must not be able to
    // reshape the path.
    const impl = respond({ id: 'x', turns: [] });
    await fetchSession({ token: 't', sessionId: '../admin', fetchImpl: impl });

    expect(impl).toHaveBeenCalledWith('/api/v1/diagnostics/..%2Fadmin', expect.anything());
  });

  it('reports a missing session as not-found', async () => {
    const result = await fetchSession({
      token: 't',
      sessionId: 'gone',
      fetchImpl: respond({}, 404),
    });

    expect(result).toEqual({ kind: 'not-found' });
  });

  it("reports another tenant's session as not-found too", async () => {
    // The API answers 404 for both on purpose, so an id someone cannot read
    // cannot be probed for existence. The client keeps that closed.
    const result = await fetchSession({
      token: 't',
      sessionId: 'theirs',
      fetchImpl: respond({}, 404),
    });

    expect(result.kind).toBe('not-found');
  });

  it('reports a body without turns as failed', async () => {
    const result = await fetchSession({
      token: 't',
      sessionId: 's',
      fetchImpl: respond({ id: 's' }),
    });

    expect(result).toEqual({ kind: 'failed' });
  });
});
