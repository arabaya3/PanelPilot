import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  draftText,
  ServerDraft,
  serverBody,
  type PreserveOutcome,
} from '@/components/design/server-draft';
import {
  deleteServerDraft,
  putServerDraft,
  reviseProject,
  type getServerDraft,
  type ServerDraftBody,
  type ServerDraftState,
} from '@/lib/design';

import { renderApp } from './helpers';

/** Slice 1 of server-backed drafts: explicit saves only. SYNTHETIC data. */

function nth<T>(items: readonly T[], index: number): T {
  const item = items[index];
  if (item === undefined) throw new Error(`no item ${String(index)}`);
  return item;
}

function body(name: string): ServerDraftBody {
  return {
    clean: '{}',
    info: { name, number: '', customer: '', consultant: '', contractor: '' },
    boards: [{ name: 'MDB', loads: [] }],
    profileText: '',
    language: null,
    market: 'jo',
    control: null,
    ai: null,
    configuration: null,
  };
}

function state(over: Partial<ServerDraftState> = {}): ServerDraftState {
  return {
    project_id: 'p1',
    token: 'd1.1',
    state: 'live',
    base_revision: 2,
    updated_at: '2026-11-19T10:00:00Z',
    body: body('Server copy'),
    replayed: false,
    ...over,
  };
}

type Get = typeof getServerDraft;
type Put = typeof putServerDraft;
type Remove = typeof deleteServerDraft;

function setup(
  options: {
    get?: Get;
    put?: Put;
    remove?: Remove;
    work?: ServerDraftBody | null;
    dirty?: boolean;
    preserve?: () => PreserveOutcome;
    latest?: number;
    locallyKept?: boolean;
  } = {},
) {
  const order: string[] = [];
  const get = vi.fn<Get>(
    options.get ??
      (() => Promise.resolve({ kind: 'found', lookup: { writes_enabled: true, draft: null } })),
  );
  const put = vi.fn<Put>(options.put ?? (() => Promise.resolve({ kind: 'failed' })));
  const remove = vi.fn<Remove>(options.remove ?? (() => Promise.resolve({ kind: 'failed' })));
  const preserveLocal = vi.fn(() => {
    order.push('preserve');
    return options.preserve?.() ?? 'kept';
  });
  const onRestore = vi.fn(() => {
    order.push('restore');
  });
  const work = options.work === undefined ? body('Mine') : options.work;
  const props = {
    token: 'tok',
    project: { id: 'p1', revision: 2, latest: options.latest ?? 2 },
    body: work,
    dirty: options.dirty ?? work !== null,
    locallyKept: options.locallyKept ?? true,
    preserveLocal,
    onRestore,
    impls: { get, put, remove },
  };
  const view = renderApp(<ServerDraft {...props} />, { locale: 'en' });
  return { get, put, remove, preserveLocal, onRestore, order, props, view };
}

describe('server drafts (slice 1)', () => {
  it('keeps the work only when asked, with no expected draft the first time', async () => {
    const { put } = setup({
      put: () => Promise.resolve({ kind: 'kept', draft: state({ body: body('Mine') }) }),
    });
    await screen.findByTestId('server-draft');
    // Nothing is sent by rendering or by the work alone.
    expect(put).not.toHaveBeenCalled();
    expect(screen.getByTestId('server-draft-status').textContent).toMatch(/Only on this device/);
    fireEvent.click(screen.getByTestId('server-draft-save'));
    await waitFor(() => {
      expect(screen.getByTestId('server-draft-status').textContent).toMatch(/Saved as draft at/);
    });
    expect(put).toHaveBeenCalledTimes(1);
    const call = nth(put.mock.calls, 0)[0];
    expect(call.ifMatch).toBeNull();
    expect(call.baseRevision).toBe(2);
    expect(call.body.info.name).toBe('Mine');
    expect(call.attempt.request_id).toMatch(/^[0-9a-f-]{36}$/);
    // Unchanged since: nothing more to save.
    expect(screen.getByTestId('server-draft-save').hasAttribute('disabled')).toBe(true);
  });

  it('says "Not saved" when this browser could not keep the work either', async () => {
    setup({ locallyKept: false });
    expect((await screen.findByTestId('server-draft-status')).textContent).toMatch(/Not saved/);
  });

  it('retries a failed attempt with the same request id, and a new save gets a new one', async () => {
    let calls = 0;
    const { put } = setup({
      put: () => {
        calls += 1;
        return Promise.resolve(
          calls === 1
            ? { kind: 'failed' as const }
            : { kind: 'kept' as const, draft: state({ body: body('Mine') }) },
        );
      },
    });
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    await screen.findByTestId('server-draft-retry');
    expect(screen.getByTestId('server-draft-note').textContent).toMatch(
      /no answer from the server/,
    );
    fireEvent.click(screen.getByTestId('server-draft-retry'));
    await waitFor(() => {
      expect(put).toHaveBeenCalledTimes(2);
    });
    const sentAll = put.mock.calls.map(([options]) => options);
    const first = nth(sentAll, 0);
    const second = nth(sentAll, 1);
    expect(second.attempt).toEqual(first.attempt);
    expect(second.ifMatch).toBe(first.ifMatch);
    await waitFor(() => {
      expect(screen.queryByTestId('server-draft-retry')).toBeNull();
    });
  });

  it('keeps a failed attempt to retry after the session ended, and says so', async () => {
    const onUnauthorized = vi.fn();
    const put = vi.fn<Put>(() => Promise.resolve({ kind: 'unauthorized' }));
    renderApp(
      <ServerDraft
        token="tok"
        project={{ id: 'p1', revision: 2, latest: 2 }}
        body={body('Mine')}
        dirty
        locallyKept
        preserveLocal={() => 'kept'}
        onRestore={vi.fn()}
        onUnauthorized={onUnauthorized}
        impls={{
          get: () =>
            Promise.resolve({ kind: 'found', lookup: { writes_enabled: true, draft: null } }),
          put,
        }}
      />,
      { locale: 'en' },
    );
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    expect((await screen.findByTestId('server-draft-note')).textContent).toMatch(
      /session has ended/,
    );
    expect(onUnauthorized).toHaveBeenCalled();
    expect(screen.getByTestId('server-draft-retry')).toBeTruthy();
  });

  it('an expired attempt is not offered again', async () => {
    setup({
      put: () => Promise.resolve({ kind: 'refused', detail: '', code: 'request_expired' }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    expect((await screen.findByTestId('server-draft-note')).textContent).toMatch(/too old/);
    expect(screen.queryByTestId('server-draft-retry')).toBeNull();
  });

  it('on a conflict shows both versions and overwrites nothing until a choice is made', async () => {
    const newer = state({ token: 'd1.5', updated_at: '2026-11-19T11:00:00Z', base_revision: 3 });
    let lookups = 0;
    const { put, onRestore, order } = setup({
      get: () => {
        lookups += 1;
        return Promise.resolve({
          kind: 'found',
          lookup: { writes_enabled: true, draft: lookups === 1 ? null : newer },
        });
      },
      put: ({ ifMatch }) =>
        Promise.resolve(
          ifMatch === 'd1.5'
            ? { kind: 'kept' as const, draft: state({ token: 'd1.6', body: body('Mine') }) }
            : {
                kind: 'refused' as const,
                detail: 'draft changed',
                code: 'draft_conflict',
                params: {
                  token: 'd1.5',
                  state: 'live',
                  base_revision: '3',
                  updated_at: newer.updated_at,
                },
              },
        ),
    });
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    const dialog = await screen.findByRole('dialog', { name: /changed elsewhere/ });
    expect(within(dialog).getByTestId('conflict-mine').textContent).toMatch(/revision 2/);
    expect(within(dialog).getByTestId('conflict-server').textContent).toMatch(
      /Server copy.*revision 3/,
    );
    expect(onRestore).not.toHaveBeenCalled();
    expect(put).toHaveBeenCalledTimes(1);
    // Keep mine: sent against the token now on the server, with a new request id.
    fireEvent.click(within(dialog).getByTestId('conflict-keep-mine'));
    await waitFor(() => {
      expect(put).toHaveBeenCalledTimes(2);
    });
    const sentAll = put.mock.calls.map(([options]) => options);
    const first = nth(sentAll, 0);
    const second = nth(sentAll, 1);
    expect(second.ifMatch).toBe('d1.5');
    expect(second.attempt.request_id).not.toBe(first.attempt.request_id);
    expect(order).toEqual([]);
  });

  it('"use the account\'s version" keeps this page\'s work in this browser first', async () => {
    const newer = state({ token: 'd1.5' });
    let lookups = 0;
    const { onRestore, order } = setup({
      get: () => {
        lookups += 1;
        return Promise.resolve({
          kind: 'found',
          lookup: { writes_enabled: true, draft: lookups === 1 ? null : newer },
        });
      },
      put: () =>
        Promise.resolve({ kind: 'refused', detail: '', code: 'draft_conflict', params: {} }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    fireEvent.click(await screen.findByTestId('conflict-use-server'));
    expect(order).toEqual(['preserve', 'restore']);
    expect(onRestore).toHaveBeenCalledWith({ body: newer.body, baseRevision: 2 });
  });

  it('replaces nothing when this browser cannot keep the work first', async () => {
    const { onRestore, preserveLocal } = setup({
      preserve: () => 'failed',
      get: () =>
        Promise.resolve({ kind: 'found', lookup: { writes_enabled: true, draft: state() } }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-restore'));
    expect(preserveLocal).toHaveBeenCalled();
    expect(onRestore).not.toHaveBeenCalled();
    expect(screen.getByTestId('server-draft-preserve-failed').textContent).toMatch(
      /Nothing was replaced/,
    );
  });

  it('an unchanged page restores without keeping anything', async () => {
    const { onRestore, preserveLocal } = setup({
      work: null,
      dirty: false,
      get: () =>
        Promise.resolve({ kind: 'found', lookup: { writes_enabled: true, draft: state() } }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-restore'));
    expect(preserveLocal).not.toHaveBeenCalled();
    expect(onRestore).toHaveBeenCalledTimes(1);
  });

  it('says a draft is older than a revision saved since, and keeps it', async () => {
    setup({
      latest: 4,
      get: () =>
        Promise.resolve({ kind: 'found', lookup: { writes_enabled: true, draft: state() } }),
    });
    expect((await screen.findByTestId('server-draft-kept')).textContent).toMatch(
      /made from revision 2\. Revision 4 is newer than revision 2/,
    );
  });

  it('deletes only against the token it showed', async () => {
    const { remove } = setup({
      get: () =>
        Promise.resolve({ kind: 'found', lookup: { writes_enabled: true, draft: state() } }),
      remove: () =>
        Promise.resolve({
          kind: 'kept',
          draft: state({ state: 'deleted', token: 'd1.2', body: null }),
        }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-delete'));
    await screen.findByText('Draft deleted.');
    expect(nth(remove.mock.calls, 0)[0].ifMatch).toBe('d1.1');
    expect(screen.queryByTestId('server-draft-kept')).toBeNull();
  });

  it('shows a draft name as text, never as markup', async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    let lookups = 0;
    setup({
      get: () => {
        lookups += 1;
        return Promise.resolve({
          kind: 'found',
          lookup: {
            writes_enabled: true,
            draft: lookups === 1 ? null : state({ body: body(hostile) }),
          },
        });
      },
      put: () => Promise.resolve({ kind: 'refused', detail: '', code: 'draft_conflict' }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    const server = await screen.findByTestId('conflict-server');
    expect(server.textContent).toContain(hostile);
    expect(document.querySelector('img')).toBeNull();
  });

  it('with writes off: nothing when there is no draft; read and delete when there is', async () => {
    const off = setup({
      get: () => Promise.resolve({ kind: 'found', lookup: { writes_enabled: false, draft: null } }),
    });
    await waitFor(() => {
      expect(off.get).toHaveBeenCalled();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByTestId('server-draft')).toBeNull();
    off.view.unmount();
    setup({
      get: () =>
        Promise.resolve({ kind: 'found', lookup: { writes_enabled: false, draft: state() } }),
    });
    await screen.findByTestId('server-draft-restore');
    expect(screen.getByTestId('server-draft-delete')).toBeTruthy();
    expect(screen.queryByTestId('server-draft-save')).toBeNull();
    expect(screen.getByText(/turned off here/)).toBeTruthy();
  });

  it('is not shown against an older API without the draft route', async () => {
    const { get } = setup({
      get: () => Promise.resolve({ kind: 'refused', detail: 'Not Found' }),
    });
    await waitFor(() => {
      expect(get).toHaveBeenCalled();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByTestId('server-draft')).toBeNull();
  });

  it('is not shown for a trial (the server needs an account)', async () => {
    const { get } = setup({
      get: () =>
        Promise.resolve({ kind: 'refused', detail: '', code: 'server_drafts_need_account' }),
    });
    await waitFor(() => {
      expect(get).toHaveBeenCalled();
    });
    expect(screen.queryByTestId('server-draft')).toBeNull();
  });

  it('a server draft body never carries whose or which project it is', () => {
    const work = serverBody({
      session: 'user-1',
      project: { id: 'p1', revision: 2 },
      clean: '{}',
      info: body('x').info,
      boards: [],
      profileText: '',
      language: null,
      market: 'jo',
    });
    expect(work).not.toHaveProperty('session');
    expect(work).not.toHaveProperty('project');
  });
});

describe('review follow-ups', () => {
  it('reads the server again after a replayed answer, rather than trusting its token', async () => {
    let lookups = 0;
    const { get, put } = setup({
      get: () => {
        lookups += 1;
        return Promise.resolve({
          kind: 'found',
          lookup: { writes_enabled: true, draft: lookups === 1 ? null : state({ token: 'd1.9' }) },
        });
      },
      put: () => Promise.resolve({ kind: 'kept', draft: state({ token: 'd1.1', replayed: true }) }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    await waitFor(() => {
      expect(get).toHaveBeenCalledTimes(2);
    });
    expect(screen.getByTestId('server-draft')).toBeTruthy();
    // The next save is made against the token now on the server.
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    await waitFor(() => {
      expect(put).toHaveBeenCalledTimes(2);
    });
    expect(nth(put.mock.calls, 1)[0].ifMatch).toBe('d1.9');
  });

  it('a renewed token keeps the attempt in flight and its retry', async () => {
    let answer: (value: Awaited<ReturnType<Put>>) => void = () => undefined;
    const { props, view, put } = setup({
      put: () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    view.rerender(<ServerDraft {...props} token="tok-2" />);
    act(() => {
      answer({ kind: 'unauthorized' });
    });
    expect((await screen.findByTestId('server-draft-note')).textContent).toMatch(
      /session has ended/,
    );
    expect(screen.getByTestId('server-draft-save').hasAttribute('disabled')).toBe(false);
    fireEvent.click(screen.getByTestId('server-draft-retry'));
    await waitFor(() => {
      expect(put).toHaveBeenCalledTimes(2);
    });
    expect(nth(put.mock.calls, 1)[0].attempt).toEqual(nth(put.mock.calls, 0)[0].attempt);
    expect(nth(put.mock.calls, 1)[0].token).toBe('tok-2');
  });
});

describe('restoring after a save in this view', () => {
  it('reads the body first (a write answer carries none), then restores it', async () => {
    let lookups = 0;
    const { onRestore, props, view } = setup({
      get: () => {
        lookups += 1;
        return Promise.resolve({
          kind: 'found',
          lookup: {
            writes_enabled: true,
            draft: lookups === 1 ? null : state({ token: 'd1.1', body: body('Kept earlier') }),
          },
        });
      },
      put: () => Promise.resolve({ kind: 'kept', draft: state({ token: 'd1.1', body: null }) }),
    });
    fireEvent.click(await screen.findByTestId('server-draft-save'));
    await screen.findByText('Saved as draft.');
    // The page changes after the save, so Restore is offered again.
    view.rerender(<ServerDraft {...props} body={body('Changed since')} />);
    fireEvent.click(await screen.findByTestId('server-draft-restore'));
    await waitFor(() => {
      expect(onRestore).toHaveBeenCalledTimes(1);
    });
    expect(onRestore).toHaveBeenCalledWith(expect.objectContaining({ baseRevision: 2 }));
  });
});

describe('comparing draft bodies', () => {
  it('ignores key order and fields the server spells as null', () => {
    expect(draftText({ b: 1, a: { d: null, c: [1, { f: 2, e: 3 }] } })).toBe(
      draftText({ a: { c: [1, { e: 3, f: 2 }] }, b: 1 }),
    );
    expect(draftText({ a: 1 })).not.toBe(draftText({ a: 2 }));
  });
});

describe('draft and revise requests', () => {
  function sentJson(sent: { init: RequestInit }[], index: number): Record<string, unknown> {
    return JSON.parse(nth(sent, index).init.body as string) as Record<string, unknown>;
  }

  function capture() {
    const sent: { url: string; init: RequestInit }[] = [];
    const fetchImpl = vi.fn((url: string | URL | Request, init?: RequestInit) => {
      sent.push({ url: url instanceof Request ? url.url : url.toString(), init: init ?? {} });
      return Promise.resolve(
        new Response(JSON.stringify(state()), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }) as unknown as typeof fetch;
    return { sent, fetchImpl };
  }

  it('PUT sends the attempt, the expected token and the body', async () => {
    const { sent, fetchImpl } = capture();
    const attempt = {
      request_id: '00000000-0000-4000-8000-000000000001',
      issued_at: '2026-11-19T10:00:00.000Z',
    };
    const outcome = await putServerDraft({
      token: 't',
      fetchImpl,
      id: 'p/1',
      attempt,
      ifMatch: null,
      baseRevision: 3,
      body: body('Mine'),
    });
    expect(outcome.kind).toBe('kept');
    expect(nth(sent, 0).url).toBe('/api/v1/design/projects/p%2F1/draft');
    expect(nth(sent, 0).init.method).toBe('PUT');
    expect(sentJson(sent, 0)).toEqual({
      ...attempt,
      if_match: null,
      base_revision: 3,
      body: body('Mine'),
    });
  });

  it('DELETE sends its attempt and token in the body', async () => {
    const { sent, fetchImpl } = capture();
    const attempt = {
      request_id: '00000000-0000-4000-8000-000000000002',
      issued_at: '2026-11-19T10:00:00.000Z',
    };
    await deleteServerDraft({ token: 't', fetchImpl, id: 'p1', attempt, ifMatch: 'd1.1' });
    expect(nth(sent, 0).init.method).toBe('DELETE');
    expect(sentJson(sent, 0)).toEqual({ ...attempt, if_match: 'd1.1' });
  });

  it('revise sends expected_revision only when given (older callers keep their contract)', async () => {
    const { sent, fetchImpl } = capture();
    const request = { boards: [] } as unknown as Parameters<typeof reviseProject>[0]['request'];
    await reviseProject({ token: 't', fetchImpl, id: 'p1', request });
    await reviseProject({ token: 't', fetchImpl, id: 'p1', request, expectedRevision: 4 });
    expect(sentJson(sent, 0)).not.toHaveProperty('expected_revision');
    expect(sentJson(sent, 1).expected_revision).toBe(4);
  });
});
