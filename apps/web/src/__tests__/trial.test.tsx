import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { components } from '@panelpilot/shared-types';

import { Chat } from '@/components/chat';
import { TrialLimitModal } from '@/components/chat/trial-limit-modal';
import type { StreamEvent } from '@/lib/diagnosis-stream';
import {
  clearTrial,
  fetchQuota,
  limitReached,
  readTrial,
  refreshTokens,
  resumeTrial,
  signupClaimingTrial,
  startTrial,
  storeTrial,
  TRIAL_STORAGE_KEY,
  type TrialSession,
} from '@/lib/trial';

import { renderApp } from './helpers';

/**
 * Tests for the self-serve trial.
 *
 * The flow the spec asks for is: anonymous question, real answer, limit hit,
 * signup, conversation preserved. Starting a trial has no endpoint yet (see
 * `lib/trial.ts`), so what is exercised here is everything downstream of that
 * — the claim, which the backend *does* implement, and the states around it.
 *
 * The claim secret is the part that matters most. The backend is explicit that
 * the session id travels in URLs and is not a credential, so a signup that
 * sent the id alone would let anyone who learned one join that tenant.
 */

type DiagnosticResponse = components['schemas']['DiagnosticResponse'];

const TRIAL: TrialSession = { sessionId: 'sess-1', claimSecret: 'secret-1' };

beforeEach(() => {
  window.localStorage.clear();
});

// --- what the browser holds ---------------------------------------------------

describe('the stored trial', () => {
  it('round-trips a trial across a reload', () => {
    storeTrial(TRIAL);
    expect(readTrial()).toEqual(TRIAL);
  });

  it('forgets it once claimed', () => {
    storeTrial(TRIAL);
    clearTrial();
    expect(readTrial()).toBeNull();
  });

  it.each([
    ['not JSON', 'nonsense'],
    ['not an object', '"a string"'],
    ['missing the secret', '{"sessionId":"s1"}'],
    ['an empty secret', '{"sessionId":"s1","claimSecret":""}'],
    ['missing the id', '{"claimSecret":"x"}'],
  ])('treats %s as no trial at all', (_label, raw) => {
    // Storage is shared with everything else on the origin and survives
    // deploys. A half-read trial is worse than none: it produces a signup
    // that tries to claim a session that does not exist, and it fails at the
    // one moment the engineer is committing.
    window.localStorage.setItem(TRIAL_STORAGE_KEY, raw);
    expect(readTrial()).toBeNull();
  });

  it('survives storage being unavailable', () => {
    // Private browsing and blocked cookies both land here.
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readTrial()).toBeNull();
    vi.restoreAllMocks();
  });

  it('starting still works when the trial cannot be persisted', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    // A trial that does not survive a reload is far better than refusing to
    // start one.
    expect(() => {
      storeTrial(TRIAL);
    }).not.toThrow();
    vi.restoreAllMocks();
  });
});

describe('limitReached', () => {
  it('is true only when nothing is left', () => {
    expect(limitReached({ questions_used: 3, question_limit: 3, questions_remaining: 0 })).toBe(
      true,
    );
    expect(limitReached({ questions_used: 2, question_limit: 3, questions_remaining: 1 })).toBe(
      false,
    );
  });

  it('treats an over-spend as reached rather than as negative headroom', () => {
    expect(limitReached({ questions_used: 4, question_limit: 3, questions_remaining: -1 })).toBe(
      true,
    );
  });
});

// --- starting one -------------------------------------------------------------

describe('startTrial', () => {
  it('reports the endpoint as unavailable rather than failing opaquely', async () => {
    // Today's real state: nothing issues an anonymous session. The landing
    // page has to say something true about that.
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    const outcome = await startTrial({ fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(outcome).toEqual({ kind: 'unavailable' });
  });

  it('reports a rate limit as its own outcome', async () => {
    // A workshop behind one address reaches it honestly; "could not start"
    // read as the product being broken.
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false, status: 429 });
    const outcome = await startTrial({ fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(outcome).toEqual({ kind: 'rate-limited' });
  });

  it('reads the session, its secret, and the token it may ask with', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: () =>
        Promise.resolve({
          session_id: 'sess-1',
          claim_secret: 'secret-1',
          access_token: 'tok-1',
          questions_remaining: 10,
          conversation_id: 'conv-1',
        }),
    });
    const outcome = await startTrial({ fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(outcome).toEqual({
      kind: 'started',
      trial: TRIAL,
      accessToken: 'tok-1',
      questionsRemaining: 10,
      conversationId: 'conv-1',
    });
  });

  it('reads a server that names no conversation as none, not as a failure', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: () =>
        Promise.resolve({
          session_id: 'sess-1',
          claim_secret: 'secret-1',
          access_token: 'tok-1',
          questions_remaining: 10,
        }),
    });
    const outcome = await startTrial({ fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(outcome).toMatchObject({ kind: 'started', conversationId: null });
  });

  it('refuses a response carrying no usable token', async () => {
    // Every diagnostics route authenticates. A start without a token would
    // render a chat input that 401s on the first question, which is worse
    // than saying the trial could not start.
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ session_id: 'sess-1', claim_secret: 'secret-1' }),
    });
    const outcome = await startTrial({ fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(outcome).toEqual({ kind: 'failed' });
  });

  it('refuses a response carrying an id but no secret', async () => {
    // The id is not a credential. A trial without a secret cannot be claimed
    // safely, so it is not a trial.
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ session_id: 'sess-1' }),
    });
    expect(await startTrial({ fetchImpl: fetchImpl as unknown as typeof fetch })).toEqual({
      kind: 'failed',
    });
  });
});

// --- resuming it ---------------------------------------------------------------

describe('resumeTrial', () => {
  function respond(status: number, payload: unknown = {}) {
    return vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(payload),
    });
  }

  const RESUMED = {
    session_id: 'sess-1',
    claim_secret: 'secret-1',
    access_token: 'tok-2',
    expires_in: 900,
    questions_remaining: 3,
    conversation_id: 'conv-1',
  };

  it('sends the pair in the body, never the URL', async () => {
    const fetchImpl = respond(200, RESUMED);
    await resumeTrial(TRIAL, { fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0] as [string, { method: string; body: string }];
    expect(url).toBe('/api/v1/auth/trial/resume');
    expect(url).not.toContain('secret-1');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ session_id: 'sess-1', claim_secret: 'secret-1' });
  });

  it('returns a fresh token for the same trial', async () => {
    expect(await resumeTrial(TRIAL, { fetchImpl: respond(200, RESUMED) })).toEqual({
      kind: 'resumed',
      trial: TRIAL,
      accessToken: 'tok-2',
      questionsRemaining: 3,
      conversationId: 'conv-1',
    });
  });

  it('takes the pair the server returned, so the token and trial always match', async () => {
    const outcome = await resumeTrial(TRIAL, {
      fetchImpl: respond(200, { ...RESUMED, session_id: 'sess-9', claim_secret: 'secret-9' }),
    });
    expect(outcome).toMatchObject({ trial: { sessionId: 'sess-9', claimSecret: 'secret-9' } });
  });

  it.each([401, 404, 405, 422])('reports a %i as the trial being gone', async (status) => {
    // Expired, claimed, or unknown: the remedy is a new trial.
    expect(await resumeTrial(TRIAL, { fetchImpl: respond(status) })).toEqual({ kind: 'gone' });
  });

  it.each([500, 502, 503])('reports a %i as a failure, not the trial being gone', async (code) => {
    // Distinct because "gone" throws the stored pair away, and a server
    // having a bad minute is no reason to lose a reachable conversation.
    expect(await resumeTrial(TRIAL, { fetchImpl: respond(code) })).toEqual({ kind: 'failed' });
  });

  it('reports a network failure as a failure', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('offline'));
    expect(await resumeTrial(TRIAL, { fetchImpl })).toEqual({ kind: 'failed' });
  });

  it('refuses a success carrying no token', async () => {
    const { access_token: _dropped, ...noToken } = RESUMED;
    expect(await resumeTrial(TRIAL, { fetchImpl: respond(200, noToken) })).toEqual({
      kind: 'failed',
    });
  });
});

// --- the quota, and renewing an account ----------------------------------------

describe('fetchQuota', () => {
  it('reads the server’s count with the bearer token', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({ questions_used: 9, question_limit: 10, questions_remaining: 1 }),
    });
    expect(await fetchQuota({ token: 'tok', fetchImpl })).toEqual({
      kind: 'loaded',
      quota: { questions_used: 9, question_limit: 10, questions_remaining: 1 },
    });
    const [url, init] = fetchImpl.mock.calls[0] as [string, { headers: Record<string, string> }];
    expect(url).toBe('/api/v1/auth/quota');
    expect(init.headers.Authorization).toBe('Bearer tok');
  });

  it('distinguishes a refused token from a failure', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await fetchQuota({ token: 'tok', fetchImpl })).toEqual({ kind: 'unauthorized' });
  });

  it('refuses a payload missing the count rather than guessing zero', async () => {
    // A guessed zero would put a signup wall in front of someone with
    // questions left.
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ questions_used: 9 }),
    });
    expect(await fetchQuota({ token: 'tok', fetchImpl })).toEqual({ kind: 'failed' });
  });
});

describe('refreshTokens', () => {
  it('exchanges a refresh token for a new pair', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ access_token: 'a2', refresh_token: 'r2' }),
    });
    expect(await refreshTokens({ refreshToken: 'r1', fetchImpl })).toEqual({
      kind: 'refreshed',
      accessToken: 'a2',
      refreshToken: 'r2',
    });
    const init = fetchImpl.mock.calls[0]?.[1] as { body: string };
    expect(JSON.parse(init.body)).toEqual({ refresh_token: 'r1' });
  });

  it('reports an expired refresh token as such', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    expect(await refreshTokens({ refreshToken: 'r1', fetchImpl })).toEqual({ kind: 'expired' });
  });
});

// --- claiming it --------------------------------------------------------------

describe('signupClaimingTrial', () => {
  function respond(status: number, payload: unknown) {
    return vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(payload),
    });
  }

  it('sends the id and the secret together', async () => {
    const fetchImpl = respond(201, { access_token: 'a', refresh_token: 'r' });
    await signupClaimingTrial({
      email: 'e@example.com',
      password: 'pw',
      trial: TRIAL,
      fetchImpl: fetchImpl,
    });

    const init = fetchImpl.mock.calls[0]?.[1] as { body: string };
    const body = JSON.parse(init.body) as Record<string, unknown>;
    expect(body.claim_session_id).toBe('sess-1');
    expect(body.claim_secret).toBe('secret-1');
  });

  it('sends neither when there is no trial to claim', async () => {
    const fetchImpl = respond(201, { access_token: 'a', refresh_token: 'r' });
    await signupClaimingTrial({
      email: 'e@example.com',
      password: 'pw',
      trial: null,
      fetchImpl: fetchImpl,
    });

    const init = fetchImpl.mock.calls[0]?.[1] as { body: string };
    const body = JSON.parse(init.body) as Record<string, unknown>;
    expect(body).not.toHaveProperty('claim_session_id');
    expect(body).not.toHaveProperty('claim_secret');
  });

  it('distinguishes an expired trial from a generic failure', async () => {
    // The remedies differ: the account can still be created, it just will not
    // carry the conversation.
    const fetchImpl = respond(404, {});
    expect(
      await signupClaimingTrial({
        email: 'e@example.com',
        password: 'pw',
        trial: TRIAL,
        fetchImpl: fetchImpl,
      }),
    ).toEqual({ kind: 'trial-gone' });
  });

  it('reports an email that already has an account', async () => {
    const fetchImpl = respond(409, {});
    expect(
      await signupClaimingTrial({
        email: 'e@example.com',
        password: 'pw',
        fetchImpl: fetchImpl,
      }),
    ).toEqual({ kind: 'email-taken' });
  });

  it('refuses a success response with no tokens in it', async () => {
    const fetchImpl = respond(201, { access_token: 'a' });
    expect(
      await signupClaimingTrial({
        email: 'e@example.com',
        password: 'pw',
        fetchImpl: fetchImpl,
      }),
    ).toEqual({ kind: 'failed' });
  });
});

// --- the modal, driven by hand -------------------------------------------------

describe('the limit modal', () => {
  it('carries the trial into the account when someone signs up', async () => {
    // The whole flow's payoff, through the real form: type an email and a
    // password, press the button, and the conversation goes with it.
    const signupImpl = vi.fn().mockResolvedValue({
      kind: 'signed-up',
      accessToken: 'a',
      refreshToken: 'r',
    }) as unknown as typeof signupClaimingTrial;
    const onSignedUp = vi.fn();

    renderApp(
      <TrialLimitModal
        trial={TRIAL}
        onSignedUp={onSignedUp}
        onDismiss={vi.fn()}
        signupImpl={signupImpl}
      />,
    );

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'e@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'hunter22' } });
    fireEvent.click(screen.getByRole('button', { name: /create account/i }));

    await waitFor(() => {
      expect(onSignedUp).toHaveBeenCalledWith({ accessToken: 'a', refreshToken: 'r' });
    });
    // And the trial went with the signup, not as a separate step.
    expect(vi.mocked(signupImpl).mock.calls[0]?.[0].trial).toEqual(TRIAL);
  });

  it('says the conversation carries over, because that is the fear', () => {
    renderApp(<TrialLimitModal trial={TRIAL} onSignedUp={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.getByText(/conversation carries over/i)).toBeTruthy();
  });

  it('names the failure rather than saying something went wrong', async () => {
    // On a signup form, a generic error is the moment someone gives up — and
    // each of these has a different remedy.
    const signupImpl = vi
      .fn()
      .mockResolvedValue({ kind: 'email-taken' }) as unknown as typeof signupClaimingTrial;

    renderApp(
      <TrialLimitModal
        trial={TRIAL}
        onSignedUp={vi.fn()}
        onDismiss={vi.fn()}
        signupImpl={signupImpl}
      />,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'e@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: /create account/i }));

    await waitFor(() => {
      expect(screen.getByTestId('trial-error')).toBeTruthy();
    });
    expect(screen.getByRole('alert').textContent).toMatch(/already has an account/i);
  });

  it('can be dismissed rather than trapping the engineer', () => {
    const onDismiss = vi.fn();
    renderApp(<TrialLimitModal trial={TRIAL} onSignedUp={vi.fn()} onDismiss={onDismiss} />);

    fireEvent.click(screen.getByRole('button', { name: /not now/i }));
    expect(onDismiss).toHaveBeenCalled();
  });

  it('dismisses on Escape', () => {
    const onDismiss = vi.fn();
    renderApp(<TrialLimitModal trial={TRIAL} onSignedUp={vi.fn()} onDismiss={onDismiss} />);

    fireEvent.keyDown(screen.getByTestId('trial-limit-modal'), { key: 'Escape' });
    expect(onDismiss).toHaveBeenCalled();
  });

  it('is a labelled modal dialog with focus in the first field', () => {
    renderApp(<TrialLimitModal trial={TRIAL} onSignedUp={vi.fn()} onDismiss={vi.fn()} />);
    const dialog = screen.getByRole('dialog');

    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.getAttribute('aria-labelledby')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByLabelText('Email'));
  });

  it('does not offer to carry a conversation it does not have', () => {
    renderApp(<TrialLimitModal trial={null} onSignedUp={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.queryByText(/conversation carries over/i)).toBeNull();
    expect(screen.getByText(/create an account to continue/i)).toBeTruthy();
  });
});

// --- the modal never cuts off an answer ---------------------------------------

describe('when the limit modal appears', () => {
  /** A stream the test releases one event at a time. */
  function controllableStream() {
    const queue: ((value: IteratorResult<StreamEvent, undefined>) => void)[] = [];
    let done = false;
    async function* generator(): AsyncGenerator<StreamEvent> {
      for (;;) {
        if (done) return;
        const next = await new Promise<IteratorResult<StreamEvent, undefined>>((resolve) => {
          queue.push(resolve);
        });
        if (next.done) return;
        yield next.value;
      }
    }
    return {
      generator,
      emit(event: StreamEvent) {
        queue.shift()?.({ done: false, value: event });
      },
      end() {
        done = true;
        queue.shift()?.({ done: true, value: undefined });
      },
    };
  }

  const RESPONSE: DiagnosticResponse = {
    session_id: 's1',
    answer: {
      text: 'x',
      citations: [
        {
          document_id: 'd1',
          document_title: 'Manual',
          manufacturer: 'ABB',
          page: null,
          section: null,
        },
      ],
    },
    diagnosis: {
      summary: 'Undervoltage.',
      summary_citation_ids: ['d1'],
      severity: 'critical',
      equipment_model: null,
      steps: [
        {
          order: 1,
          instruction: 'Measure the supply.',
          rationale: 'r',
          citation_ids: ['d1'],
          severity: 'critical',
        },
      ],
    },
    confidence: {
      overall: 0.9,
      retrieval_score: 0.9,
      passage_agreement: 0.9,
      citation_density: 0.9,
    },
    low_confidence: false,
    refusal_message: null,
  };

  it('waits for the answer to finish before asking for an email', async () => {
    // The rule the spec singles out. Cutting off a diagnosis to ask for an
    // email is a worse version of the funnel this whole flow exists to avoid
    // — and the engineer loses the answer they were reading.
    const stream = controllableStream();
    renderApp(
      <Chat token="t" streamImpl={stream.generator} trial={TRIAL} questionsRemaining={0} />,
    );

    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Why is it tripping?' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    // A turn is in flight, and the modal stays away even though the quota is
    // already spent.
    await waitFor(() => {
      expect(screen.getByTestId('assistant-progress')).toBeTruthy();
    });
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();

    stream.emit({ kind: 'result', response: RESPONSE });
    stream.end();

    // Only once the answer has landed.
    await waitFor(() => {
      expect(screen.getByTestId('trial-limit-modal')).toBeTruthy();
    });
    // And the answer is still there behind it.
    expect(screen.getByTestId('diagnostic-card')).toBeTruthy();
  });

  it('stays away while questions remain', () => {
    renderApp(<Chat token="t" trial={TRIAL} questionsRemaining={2} />);
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();
  });

  it('stays away when the caller does not know the quota', () => {
    // `null` means unknown, not zero. Showing a signup wall because a quota
    // request failed would be the worst possible misreading.
    renderApp(<Chat token="t" trial={TRIAL} />);
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();
  });

  it('asks the server for the count after each answer, and appears when it hits zero', async () => {
    // The count used to be read once at start and never again, so the modal
    // could only appear for a visitor who arrived with nothing left.
    const stream = controllableStream();
    const quotaImpl = vi.fn().mockResolvedValue({
      kind: 'loaded',
      quota: { questions_used: 10, question_limit: 10, questions_remaining: 0 },
    });
    renderApp(
      <Chat
        token="tok"
        streamImpl={stream.generator}
        trial={TRIAL}
        questionsRemaining={1}
        quotaImpl={quotaImpl}
      />,
    );
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();

    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Why is it tripping?' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    await waitFor(() => {
      expect(screen.getByTestId('assistant-progress')).toBeTruthy();
    });
    stream.emit({ kind: 'result', response: RESPONSE });
    stream.end();

    await waitFor(() => {
      expect(screen.getByTestId('trial-limit-modal')).toBeTruthy();
    });
    expect(quotaImpl).toHaveBeenCalledWith({ token: 'tok' });
  });

  it('keeps the count it had when the quota check fails', async () => {
    const stream = controllableStream();
    const quotaImpl = vi.fn().mockResolvedValue({ kind: 'failed' });
    renderApp(
      <Chat
        token="tok"
        streamImpl={stream.generator}
        trial={TRIAL}
        questionsRemaining={3}
        quotaImpl={quotaImpl}
      />,
    );
    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Why is it tripping?' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    await waitFor(() => {
      expect(screen.getByTestId('assistant-progress')).toBeTruthy();
    });
    stream.emit({ kind: 'result', response: RESPONSE });
    stream.end();

    await waitFor(() => {
      expect(quotaImpl).toHaveBeenCalled();
    });
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();
  });

  it('offers signup when the server refuses a question for being over the limit', async () => {
    // The client's count can be stale — another tab, or the server's own
    // accounting. The refusal is the authority, and it means signup, not
    // "the server could not start this answer".
    const stream = controllableStream();
    renderApp(
      <Chat
        token="tok"
        streamImpl={stream.generator}
        trial={TRIAL}
        questionsRemaining={5}
        quotaImpl={vi.fn().mockResolvedValue({ kind: 'failed' })}
      />,
    );
    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Why is it tripping?' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    await waitFor(() => {
      expect(screen.getByTestId('assistant-progress')).toBeTruthy();
    });
    stream.emit({ kind: 'interrupted', reason: 'quota-exhausted' });
    stream.end();

    await waitFor(() => {
      expect(screen.getByTestId('trial-limit-modal')).toBeTruthy();
    });
  });

  it('hands a refused token to the caller rather than only showing an error', async () => {
    const stream = controllableStream();
    const onUnauthorized = vi.fn();
    renderApp(
      <Chat
        token="tok"
        streamImpl={stream.generator}
        trial={TRIAL}
        onUnauthorized={onUnauthorized}
      />,
    );
    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Why is it tripping?' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    await waitFor(() => {
      expect(screen.getByTestId('assistant-progress')).toBeTruthy();
    });
    stream.emit({ kind: 'interrupted', reason: 'unauthorized' });
    stream.end();

    await waitFor(() => {
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
    });
  });

  it('does not come back after it is dismissed', () => {
    // "Not now" has to mean not now, or the modal becomes the gate it was
    // written to avoid being.
    renderApp(<Chat token="t" trial={TRIAL} questionsRemaining={0} />);
    expect(screen.getByTestId('trial-limit-modal')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /not now/i }));
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();
  });
});

// --- the trial's own conversation, and an account at its limit -------------

describe('the conversation a trial opened', () => {
  function recorder(events: StreamEvent[]) {
    const requests: { session_id?: string | null }[] = [];
    async function* streamImpl(options: {
      request: { session_id?: string | null };
    }): AsyncGenerator<StreamEvent> {
      requests.push(options.request);
      await Promise.resolve();
      for (const event of events) yield event;
    }
    return { requests, streamImpl };
  }

  function ask(question: string) {
    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: question } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);
  }

  const EMPTY_CONVERSATION = {
    kind: 'loaded' as const,
    session: {
      id: 'conv-1',
      turns: [],
      created_at: '2026-09-29T00:00:00Z',
      updated_at: '2026-09-29T00:00:00Z',
    } as unknown as components['schemas']['DiagnosticSession'],
  };

  it('asks the first question in it rather than opening another', async () => {
    // Every trial used to leave an empty "New conversation" in the history:
    // the trial opened one, and the first question opened a second.
    const fetchSessionImpl = vi.fn().mockResolvedValue(EMPTY_CONVERSATION);
    const { requests, streamImpl } = recorder([]);
    renderApp(
      <Chat
        token="t"
        trial={TRIAL}
        questionsRemaining={5}
        conversationId="conv-1"
        fetchSessionImpl={fetchSessionImpl}
        streamImpl={streamImpl as never}
      />,
    );

    await waitFor(() => {
      expect(fetchSessionImpl).toHaveBeenCalledWith({ token: 't', sessionId: 'conv-1' });
    });
    ask('Why is it tripping?');

    await waitFor(() => {
      expect(requests[0]?.session_id).toBe('conv-1');
    });
  });

  it('opens it once, not again when the token is renewed', async () => {
    const fetchSessionImpl = vi.fn().mockResolvedValue(EMPTY_CONVERSATION);
    const view = renderApp(
      <Chat token="t" trial={TRIAL} conversationId="conv-1" fetchSessionImpl={fetchSessionImpl} />,
    );
    await waitFor(() => {
      expect(fetchSessionImpl).toHaveBeenCalledTimes(1);
    });

    view.rerender(
      <Chat
        token="t-renewed"
        trial={TRIAL}
        conversationId="conv-1"
        fetchSessionImpl={fetchSessionImpl}
      />,
    );
    await Promise.resolve();

    expect(fetchSessionImpl).toHaveBeenCalledTimes(1);
  });

  it('tells an account it has used its questions, without offering signup', async () => {
    // After signup the account is in the trial's tenant, with its allowance.
    // The card said "create an account to keep asking" and the signup form
    // opened again -- to someone who had just made one.
    const { streamImpl } = recorder([{ kind: 'interrupted', reason: 'quota-exhausted' }]);
    renderApp(<Chat token="t" trial={null} streamImpl={streamImpl as never} />);

    ask('One more question');

    await waitFor(() => {
      expect(screen.getByText(/free questions on this account/i)).toBeTruthy();
    });
    expect(screen.queryByText(/create an account/i)).toBeNull();
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();
  });

  it('never shows an account the signup form, whatever its count says', () => {
    // Signing up from an account would make a second one, not continue this.
    renderApp(<Chat token="t" trial={null} questionsRemaining={0} />);
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();
  });

  it('still offers signup to a trial refused for its quota', async () => {
    const { streamImpl } = recorder([{ kind: 'interrupted', reason: 'quota-exhausted' }]);
    renderApp(<Chat token="t" trial={TRIAL} streamImpl={streamImpl as never} />);

    ask('One more question');

    await waitFor(() => {
      expect(screen.getByTestId('trial-limit-modal')).toBeTruthy();
    });
  });
});
