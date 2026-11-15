import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  AccountSessionProvider,
  useAccountSession,
  type AccountState,
} from '@/components/account-session';
import { DesignScreen } from '@/components/design-screen';
import type { listProjects, ProjectPage } from '@/lib/design';
import type { signIn, signOutEverywhere } from '@/lib/auth';
import type { acquireTrial } from '@/lib/session';
import { TRIAL_STORAGE_KEY } from '@/lib/trial';

import { renderApp } from './helpers';

/**
 * /design's session controls: signing in from a trial that has saved
 * projects asks first, because an existing account does not take them; and
 * a signed-in account can sign out of every device.
 */

function jwt(sub: string, tid: string): string {
  const body = btoa(JSON.stringify({ sub, tid }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `header.${body}.signature`;
}

const TRIAL_TOKEN = jwt('anon-1', 'trial-tenant');
const ACCOUNT_TOKEN = jwt('user-1', 'account-tenant');
const TRIAL = { sessionId: 's1', claimSecret: 'claim-secret' };

function page(count: number): ProjectPage {
  return {
    projects: Array.from({ length: count }, (_, n) => ({
      id: `p${String(n)}`,
      name: `Project ${String(n)}`,
      revision: 1,
      updated_at: '2026-10-02T10:00:00Z',
    })),
    next_cursor: null,
  } as unknown as ProjectPage;
}

let shared: ReturnType<typeof useAccountSession> = null;
function Probe() {
  shared = useAccountSession();
  return null;
}

function renderScreen({
  initial = { status: 'signed-out' },
  listProjectsImpl = vi.fn<typeof listProjects>().mockResolvedValue({
    kind: 'listed',
    page: page(0),
  }),
  signInImpl = vi.fn<typeof signIn>(),
  signOutEverywhereImpl = vi.fn<typeof signOutEverywhere>(),
}: {
  initial?: AccountState;
  listProjectsImpl?: ReturnType<typeof vi.fn<typeof listProjects>>;
  signInImpl?: ReturnType<typeof vi.fn<typeof signIn>>;
  signOutEverywhereImpl?: ReturnType<typeof vi.fn<typeof signOutEverywhere>>;
} = {}) {
  window.localStorage.setItem(TRIAL_STORAGE_KEY, JSON.stringify(TRIAL));
  const acquireImpl = vi.fn<typeof acquireTrial>().mockResolvedValue({
    kind: 'ready',
    accessToken: TRIAL_TOKEN,
    trial: TRIAL,
    questionsRemaining: 5,
    conversationId: null,
  });
  renderApp(
    <AccountSessionProvider
      initial={initial}
      signOutEverywhereImpl={signOutEverywhereImpl}
      signOutImpl={vi.fn().mockResolvedValue({ kind: 'signed-out' })}
    >
      <Probe />
      <DesignScreen
        acquireImpl={acquireImpl}
        listProjectsImpl={listProjectsImpl}
        marketsImpl={vi.fn().mockResolvedValue(null)}
        signInImpl={signInImpl}
      />
    </AccountSessionProvider>,
  );
  return { listProjectsImpl, acquireImpl, signInImpl, signOutEverywhereImpl };
}

async function askToSignIn() {
  const trialBar = within(await screen.findByTestId('design-trial-mode'));
  await waitFor(() => {
    expect(screen.queryByText('Starting your trial…')).toBeNull();
  });
  fireEvent.click(trialBar.getByText('Sign in to save to your account'));
}

afterEach(() => {
  vi.clearAllMocks();
  shared = null;
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
});

describe('signing in from a trial', () => {
  it('asks first when the trial has saved projects, saying they stay with the trial', async () => {
    const { listProjectsImpl } = renderScreen({
      listProjectsImpl: vi
        .fn<typeof listProjects>()
        .mockResolvedValue({ kind: 'listed', page: page(2) }),
    });
    await askToSignIn();

    const confirm = within(await screen.findByTestId('design-trial-sign-in-confirm'));
    expect(listProjectsImpl).toHaveBeenCalledWith({ token: TRIAL_TOKEN });
    expect(confirm.getByText(/does not move this trial.s saved projects/)).toBeTruthy();
    expect(screen.queryByTestId('sign-in-form')).toBeNull();

    fireEvent.click(confirm.getByText('Sign in anyway'));
    expect(await screen.findByTestId('sign-in-form')).toBeTruthy();
    expect(screen.queryByTestId('design-trial-sign-in-confirm')).toBeNull();
  });

  it('can be called off, or turned into creating an account that keeps them', async () => {
    renderScreen({
      listProjectsImpl: vi
        .fn<typeof listProjects>()
        .mockResolvedValue({ kind: 'listed', page: page(1) }),
    });
    await askToSignIn();
    fireEvent.click(
      within(await screen.findByTestId('design-trial-sign-in-confirm')).getByText('Cancel'),
    );
    expect(screen.queryByTestId('design-trial-sign-in-confirm')).toBeNull();
    expect(screen.queryByTestId('sign-in-form')).toBeNull();

    await askToSignIn();
    fireEvent.click(
      within(await screen.findByTestId('design-trial-sign-in-confirm')).getByText(
        'Create an account instead',
      ),
    );
    expect(await screen.findByTestId('trial-limit-modal')).toBeTruthy();
  });

  it('asks too when the trial’s projects could not be listed', async () => {
    renderScreen({
      listProjectsImpl: vi.fn<typeof listProjects>().mockResolvedValue({ kind: 'failed' }),
    });
    await askToSignIn();
    expect(await screen.findByTestId('design-trial-sign-in-confirm')).toBeTruthy();
  });

  it('goes straight to the form when the trial has nothing saved', async () => {
    renderScreen();
    await askToSignIn();
    expect(await screen.findByTestId('sign-in-form')).toBeTruthy();
    expect(screen.queryByTestId('design-trial-sign-in-confirm')).toBeNull();
  });

  it('keeps the trial in this browser after signing in, to continue as later', async () => {
    const signInImpl = vi
      .fn<typeof signIn>()
      .mockResolvedValue({ kind: 'signed-in', accessToken: ACCOUNT_TOKEN });
    renderScreen({
      signInImpl,
      listProjectsImpl: vi
        .fn<typeof listProjects>()
        .mockResolvedValue({ kind: 'listed', page: page(1) }),
    });
    await askToSignIn();
    fireEvent.click(
      within(await screen.findByTestId('design-trial-sign-in-confirm')).getByText('Sign in anyway'),
    );
    const form = within(await screen.findByTestId('sign-in-form'));
    fireEvent.change(form.getByLabelText('Email'), { target: { value: 'eng@example.com' } });
    fireEvent.change(form.getByLabelText('Password'), { target: { value: 'correct horse' } });
    fireEvent.click(form.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByTestId('design-account-mode')).toBeTruthy();

    // Not claimed, not moved: the trial is still this browser's to return to.
    expect(JSON.parse(window.localStorage.getItem(TRIAL_STORAGE_KEY) ?? 'null')).toEqual(TRIAL);
  });
});

describe('signing out everywhere', () => {
  const SIGNED_IN: AccountState = { status: 'signed-in', tokens: { accessToken: ACCOUNT_TOKEN } };

  it('ends every session through the server, then signs this page out', async () => {
    const signOutEverywhereImpl = vi
      .fn<typeof signOutEverywhere>()
      .mockResolvedValue({ kind: 'signed-out' });
    renderScreen({ initial: SIGNED_IN, signOutEverywhereImpl });
    fireEvent.click(await screen.findByTestId('design-sign-out-everywhere'));
    await waitFor(() => {
      expect(shared?.state).toEqual({ status: 'signed-out' });
    });
    expect(signOutEverywhereImpl).toHaveBeenCalledWith({ accessToken: ACCOUNT_TOKEN });
    expect(await screen.findByTestId('design-signed-out')).toBeTruthy();
  });

  it('says so, and stays signed in, when the server could not do it', async () => {
    const signOutEverywhereImpl = vi
      .fn<typeof signOutEverywhere>()
      .mockResolvedValue({ kind: 'failed' });
    renderScreen({ initial: SIGNED_IN, signOutEverywhereImpl });
    fireEvent.click(await screen.findByTestId('design-sign-out-everywhere'));
    expect(await screen.findByTestId('design-account-sign-out-everywhere-failed')).toBeTruthy();
    expect(shared?.state).toEqual(SIGNED_IN);
  });
});
