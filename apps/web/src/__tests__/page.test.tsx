import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderApp } from './helpers';

import HomePage from '@/app/page';
import * as stream from '@/lib/diagnosis-stream';
import * as sessions from '@/lib/sessions';
import * as trial from '@/lib/trial';

/**
 * Tests for the landing page.
 *
 * This is the app's front door, and for most of the project's life it rendered
 * a static sample card while the real chat surface sat in `src/components/chat`
 * imported by nothing but tests. These tests exist so that cannot happen
 * quietly again: they assert the page actually mounts the chat, and that every
 * way starting a trial can fail says something true instead of leaving an
 * input that does nothing.
 */

function mockStart(outcome: trial.TrialStart) {
  return vi.spyOn(trial, 'startTrial').mockResolvedValue(outcome);
}

const STARTED: trial.TrialStart = {
  kind: 'started',
  trial: { sessionId: 'sess-1', claimSecret: 'secret-1' },
  accessToken: 'tok-1',
  questionsRemaining: 10,
  conversationId: null,
};

const EXISTING: trial.TrialSession = { sessionId: 'existing-1', claimSecret: 'existing-secret' };

function holdTrial(held: trial.TrialSession = EXISTING) {
  window.localStorage.setItem(trial.TRIAL_STORAGE_KEY, JSON.stringify(held));
}

function storedTrial(): unknown {
  const raw = window.localStorage.getItem(trial.TRIAL_STORAGE_KEY);
  return raw === null ? null : JSON.parse(raw);
}

/**
 * The history list, spied so the token the chat is running with is
 * observable: the sidebar asks for history with it on mount and again
 * whenever it changes.
 */
function spyHistory() {
  return vi.spyOn(sessions, 'listSessions').mockResolvedValue({ kind: 'unavailable' });
}

function tokensSeen(list: ReturnType<typeof spyHistory>): string[] {
  return list.mock.calls.map(([options]) => options.token);
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('HomePage', () => {
  it('renders the product name', () => {
    mockStart({ kind: 'unavailable' });
    renderApp(<HomePage />, { theme: 'light' });

    expect(screen.getByText('PanelPilot')).toBeTruthy();
  });

  it('starts a trial on mount rather than on first keystroke', async () => {
    // Starting lazily would put a round trip between pressing enter and
    // anything happening, which reads as the product being slow at exactly
    // the moment it is being judged.
    const start = mockStart(STARTED);
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(start).toHaveBeenCalled();
    });
  });

  it('mounts the real chat surface once a trial is running', async () => {
    // The regression this file exists for. A static card here is what shipped
    // for most of the project while the tested chat component was reachable
    // from nowhere.
    mockStart(STARTED);
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('chat')).toBeTruthy();
    });
  });

  it('says a trial is unavailable rather than showing a dead input', async () => {
    // `startTrial` reports `unavailable` when the endpoint is absent. An input
    // that silently does nothing is what an engineer at a panel would
    // otherwise be left with.
    mockStart({ kind: 'unavailable' });
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('landing-unavailable')).toBeTruthy();
    });
    expect(screen.queryByTestId('chat')).toBeNull();
  });

  it('offers a retry when starting a trial broke', async () => {
    // Distinct from `unavailable`: one is "not built yet", the other is "try
    // again", and they need different words.
    mockStart({ kind: 'failed' });
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('landing-failed')).toBeTruthy();
    });
  });

  it('announces a failure to a screen reader rather than only colouring it', async () => {
    mockStart({ kind: 'failed' });
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('landing-failed').getAttribute('role')).toBe('alert');
    });
  });

  it('resumes a trial this browser already holds instead of starting another', async () => {
    // Starting a second would strand the first conversation under a tenant the
    // visitor can no longer reach, and burn a fresh quota for no reason.
    holdTrial();
    const resume = vi.spyOn(trial, 'resumeTrial').mockResolvedValue({
      kind: 'resumed',
      trial: EXISTING,
      accessToken: 'tok-resumed',
      questionsRemaining: 4,
      conversationId: null,
    });
    const start = mockStart(STARTED);
    const list = spyHistory();
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('chat')).toBeTruthy();
    });
    expect(resume).toHaveBeenCalledWith(EXISTING);
    expect(start).not.toHaveBeenCalled();
    expect(storedTrial()).toEqual(EXISTING);
    await waitFor(() => {
      expect(tokensSeen(list)).toContain('tok-resumed');
    });
  });

  it('never pairs a stored trial with a new trial’s token', async () => {
    // The regression. A reload used to start a new trial and then store the
    // *old* claim pair beside the *new* token, so the signup claimed a tenant
    // other than the one every question had been asked in.
    holdTrial();
    vi.spyOn(trial, 'resumeTrial').mockResolvedValue({ kind: 'gone' });
    mockStart(STARTED);
    const list = spyHistory();
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('chat')).toBeTruthy();
    });
    // The new trial's pair, with the new trial's token.
    expect(storedTrial()).toEqual({ sessionId: 'sess-1', claimSecret: 'secret-1' });
    await waitFor(() => {
      expect(tokensSeen(list)).toContain('tok-1');
    });
  });

  it('forgets a trial the server no longer knows and starts a new one', async () => {
    holdTrial();
    vi.spyOn(trial, 'resumeTrial').mockResolvedValue({ kind: 'gone' });
    const clear = vi.spyOn(trial, 'clearTrial');
    const start = mockStart(STARTED);
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(start).toHaveBeenCalled();
    });
    expect(clear).toHaveBeenCalled();
  });

  it('keeps the stored trial and offers a retry when resuming broke', async () => {
    // A network failure is not the trial being gone. Throwing the pair away
    // here would lose a conversation that is still perfectly reachable once
    // the connection comes back.
    holdTrial();
    vi.spyOn(trial, 'resumeTrial').mockResolvedValue({ kind: 'failed' });
    const start = mockStart(STARTED);
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('landing-failed')).toBeTruthy();
    });
    expect(start).not.toHaveBeenCalled();
    expect(storedTrial()).toEqual(EXISTING);
  });

  it('stores the trial it started', async () => {
    mockStart(STARTED);
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('chat')).toBeTruthy();
    });
    expect(storedTrial()).toEqual({ sessionId: 'sess-1', claimSecret: 'secret-1' });
  });

  it('switches to the account’s token after signup, and forgets the trial', async () => {
    // The signup result used to be discarded: the chat kept asking with the
    // trial's token, and the claimed trial stayed in storage to be "resumed"
    // on the next reload.
    mockStart({ ...STARTED, questionsRemaining: 0 });
    vi.spyOn(trial, 'signupClaimingTrial').mockResolvedValue({
      kind: 'signed-up',
      accessToken: 'acct-tok',
      refreshToken: 'refresh-tok',
    });
    const list = spyHistory();
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('trial-limit-modal')).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'e@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'hunter22' } });
    fireEvent.click(screen.getByRole('button', { name: /create account/i }));

    await waitFor(() => {
      expect(tokensSeen(list)).toContain('acct-tok');
    });
    expect(storedTrial()).toBeNull();
    expect(screen.queryByTestId('trial-limit-modal')).toBeNull();
    // Neither credential is left behind in storage.
    const everything = JSON.stringify(window.localStorage);
    expect(everything).not.toContain('acct-tok');
    expect(everything).not.toContain('refresh-tok');
  });

  it('gets a fresh token when a question is refused as unauthorized', async () => {
    // The trial's token expired mid-visit. Resuming the stored trial replaces
    // it without unmounting the chat, so the transcript survives and the
    // failed turn can simply be retried.
    holdTrial();
    const resume = vi
      .spyOn(trial, 'resumeTrial')
      .mockResolvedValueOnce({
        kind: 'resumed',
        trial: EXISTING,
        accessToken: 'tok-old',
        questionsRemaining: 5,
        conversationId: null,
      })
      .mockResolvedValueOnce({
        kind: 'resumed',
        trial: EXISTING,
        accessToken: 'tok-new',
        questionsRemaining: 5,
        conversationId: null,
      });
    vi.spyOn(stream, 'streamDiagnosis').mockImplementation(async function* () {
      await Promise.resolve();
      yield { kind: 'interrupted', reason: 'unauthorized' } as const;
    });
    const list = spyHistory();
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('chat')).toBeTruthy();
    });
    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Why is it tripping?' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    await waitFor(() => {
      expect(tokensSeen(list)).toContain('tok-new');
    });
    expect(resume).toHaveBeenCalledTimes(2);
    // The same surface, with the failed turn still on it.
    expect(screen.getByTestId('assistant-failure').getAttribute('data-failure')).toBe(
      'unauthorized',
    );
  });

  it('never puts the access token in storage', async () => {
    // The claim pair has to survive a reload; a bearer token does not, and
    // leaving one on a shared workshop terminal is a disclosure nobody asked
    // for.
    mockStart(STARTED);
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('chat')).toBeTruthy();
    });

    const everything = JSON.stringify(window.localStorage);
    expect(everything).not.toContain('tok-1');
  });
});
