import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { components } from '@panelpilot/shared-types';

import { Chat } from '@/components/chat';
import { TrialLimitModal } from '@/components/chat/trial-limit-modal';
import type { StreamEvent } from '@/lib/diagnosis-stream';
import {
  clearTrial,
  limitReached,
  readTrial,
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
        }),
    });
    const outcome = await startTrial({ fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(outcome).toEqual({
      kind: 'started',
      trial: TRIAL,
      accessToken: 'tok-1',
      questionsRemaining: 10,
    });
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

  it('does not come back after it is dismissed', () => {
    // "Not now" has to mean not now, or the modal becomes the gate it was
    // written to avoid being.
    renderApp(<Chat token="t" trial={TRIAL} questionsRemaining={0} />);
    expect(screen.getByTestId('trial-limit-modal')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /not now/i }));
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();
  });
});
