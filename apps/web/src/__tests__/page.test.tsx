import { screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderApp } from './helpers';

import HomePage from '@/app/page';
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
};

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

  it('reuses a trial this browser already holds', async () => {
    // Starting a second would strand the first conversation under a tenant the
    // visitor can no longer reach, and burn a fresh quota for no reason.
    window.localStorage.setItem(
      trial.TRIAL_STORAGE_KEY,
      JSON.stringify({ sessionId: 'existing-1', claimSecret: 'existing-secret' }),
    );
    mockStart(STARTED);
    renderApp(<HomePage />, { theme: 'light' });

    await waitFor(() => {
      expect(screen.getByTestId('chat')).toBeTruthy();
    });

    const stored: unknown = JSON.parse(
      window.localStorage.getItem(trial.TRIAL_STORAGE_KEY) ?? '{}',
    );
    expect((stored as { sessionId: string }).sessionId).toBe('existing-1');
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
