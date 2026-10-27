import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AccountSessionProvider,
  tokenIdentity,
  useAccountSession,
  type AccountState,
} from '@/components/account-session';
import { DesignScreen } from '@/components/design-screen';
import { DRAFT_STORAGE_KEY } from '@/components/design/draft';
import { approvalOf, ProjectsPanel } from '@/components/design/projects-panel';
import type {
  listProjects,
  openProject,
  reviseProject,
  saveProject,
  SavedProject,
  SavedRequest,
} from '@/lib/design';
import type * as DesignModule from '@/lib/design';
import type { refreshSession, signIn } from '@/lib/auth';
import type { acquireTrial } from '@/lib/session';
import { TRIAL_STORAGE_KEY, type signupClaimingTrial } from '@/lib/trial';

import { renderApp } from './helpers';

/**
 * /design under the signed-in account, as an explicit trial, across a
 * sign-in or signup, when the session ends, and after signing out.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  save: vi.fn<typeof saveProject>(),
  revise: vi.fn<typeof reviseProject>(),
  open: vi.fn<typeof openProject>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return {
    ...actual,
    listProjects: api.list,
    saveProject: api.save,
    reviseProject: api.revise,
    openProject: api.open,
  };
});

/** An access token shaped as the API issues them (unsigned: the page never verifies one). */
function jwt(sub: string, tid: string, nonce = ''): string {
  const body = btoa(JSON.stringify({ sub, tid, nonce }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `header.${body}.signature`;
}

const TRIAL_TOKEN = jwt('anon-1', 'trial-tenant');
const ACCOUNT_TOKEN = jwt('user-1', 'account-tenant');

function trialReady() {
  return {
    kind: 'ready' as const,
    accessToken: TRIAL_TOKEN,
    trial: { sessionId: 's1', claimSecret: 'claim-secret' },
    questionsRemaining: 5,
    conversationId: null,
  };
}

const SAVED_REQUEST = {
  info: { name: 'Tower', number: '', customer: '', consultant: '', contractor: '' },
  boards: [
    {
      name: 'MDB',
      location: null,
      fed_from: null,
      feeder_length_m: null,
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [],
    },
  ],
  profile: null,
} as unknown as SavedRequest;

function saved(id = 'p1'): SavedProject {
  return {
    id,
    name: 'Tower',
    revision: 1,
    validated: false,
    export_status: 'not_validated',
    revisions: [
      {
        number: 1,
        note: '',
        author: 'e',
        approver_account_recorded: false,
        created_at: '2026-10-02T10:00:00Z',
        validated: false,
        export_status: 'not_validated' as const,
      },
    ],
    request: SAVED_REQUEST,
  };
}

/** The app's shared session, and a probe to read it from outside the page. */
let shared: ReturnType<typeof useAccountSession> = null;
function Probe() {
  shared = useAccountSession();
  return null;
}

function renderScreen({
  initial = { status: 'signed-out' },
  refreshImpl = vi.fn<typeof refreshSession>().mockResolvedValue({ kind: 'expired' }),
  acquireImpl = vi.fn<typeof acquireTrial>().mockResolvedValue(trialReady()),
  signInImpl = vi.fn<typeof signIn>(),
  signupImpl = vi.fn<typeof signupClaimingTrial>(),
}: {
  initial?: AccountState;
  refreshImpl?: ReturnType<typeof vi.fn<typeof refreshSession>>;
  acquireImpl?: ReturnType<typeof vi.fn<typeof acquireTrial>>;
  signInImpl?: ReturnType<typeof vi.fn<typeof signIn>>;
  signupImpl?: ReturnType<typeof vi.fn<typeof signupClaimingTrial>>;
} = {}) {
  const wrap = (children: ReactNode) => (
    <AccountSessionProvider initial={initial} refreshImpl={refreshImpl}>
      <Probe />
      {children}
    </AccountSessionProvider>
  );
  renderApp(
    wrap(
      <DesignScreen
        acquireImpl={acquireImpl}
        marketsImpl={vi.fn().mockResolvedValue(null)}
        signInImpl={signInImpl}
        signupImpl={signupImpl}
      />,
    ),
  );
  return { refreshImpl, acquireImpl, signInImpl, signupImpl };
}

const signedIn = (accessToken = ACCOUNT_TOKEN): AccountState => ({
  status: 'signed-in',
  tokens: { accessToken },
});

const overview = () => within(screen.getByTestId('view-overview'));
const projects = () => within(screen.getByTestId('projects-panel'));
const nameField = () => overview().getByLabelText<HTMLInputElement>('Project name');
const loadRow = () => within(screen.getByTestId('design-load-0'));

function type(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

async function completeForm() {
  await screen.findByTestId('projects-panel');
  type(nameField(), 'Tower');
  type(loadRow().getByLabelText('Circuit'), 'Lights');
  type(loadRow().getByLabelText(/^Power.*kW/), '1');
}

function storedDraft(): Record<string, unknown> | null {
  const raw = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
  return raw === null ? null : (JSON.parse(raw) as Record<string, unknown>);
}

async function signInThroughForm(accessToken: string) {
  const form = within(await screen.findByTestId('sign-in-form'));
  type(form.getByLabelText('Email'), 'eng@example.com');
  type(form.getByLabelText('Password'), 'correct horse');
  fireEvent.click(form.getByRole('button', { name: 'Sign in' }));
  await waitFor(() => {
    expect(screen.getByTestId('design-account-mode')).toBeTruthy();
  });
  return accessToken;
}

beforeEach(() => {
  shared = null;
  api.list.mockResolvedValue({ kind: 'listed', page: { projects: [] } });
});

afterEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
});

describe('signed in', () => {
  it("works under the account's token and never starts a trial", async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: saved() });
    const { acquireImpl } = renderScreen({ initial: signedIn() });
    expect(await screen.findByTestId('design-account-mode')).toBeTruthy();
    expect(screen.queryByTestId('design-trial-mode')).toBeNull();
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    await waitFor(() => {
      expect(api.save).toHaveBeenCalledTimes(1);
    });
    expect(api.save.mock.calls[0]?.[0].token).toBe(ACCOUNT_TOKEN);
    expect(api.list.mock.calls.every(([call]) => call.token === ACCOUNT_TOKEN)).toBe(true);
    expect(acquireImpl).not.toHaveBeenCalled();
  });

  it('keeps a draft for the account, never its token', async () => {
    renderScreen({ initial: signedIn() });
    await screen.findByTestId('projects-panel');
    type(nameField(), 'Kept');
    await waitFor(() => {
      expect(storedDraft()).toMatchObject({ session: 'account:user-1', info: { name: 'Kept' } });
    });
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY)).not.toContain(ACCOUNT_TOKEN);
  });
});

describe('the trial', () => {
  it('says it is a trial whose saves are not an account’s, and saves under the trial', async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: saved() });
    const { acquireImpl } = renderScreen();
    const notice = await screen.findByTestId('design-trial-mode');
    expect(notice.textContent).toContain("belong to this browser's trial, not to an account");
    expect(within(notice).getByText('Sign in to save to your account')).toBeTruthy();
    expect(acquireImpl).toHaveBeenCalledTimes(1);
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    await waitFor(() => {
      expect(api.save.mock.calls[0]?.[0].token).toBe(TRIAL_TOKEN);
    });
  });

  it('signing in keeps what was typed, and lets go of the trial’s project instead of moving it', async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: saved('trial-project') });
    const signInImpl = vi.fn<typeof signIn>().mockResolvedValue({
      kind: 'signed-in',
      accessToken: ACCOUNT_TOKEN,
    });
    renderScreen({ signInImpl });
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    await screen.findByTestId('project-opened');

    fireEvent.click(
      within(screen.getByTestId('design-trial-mode')).getByText('Sign in to save to your account'),
    );
    await signInThroughForm(ACCOUNT_TOKEN);

    expect(await screen.findByTestId('design-account-trial-project-stays')).toBeTruthy();
    expect(screen.queryByTestId('project-opened')).toBeNull();
    expect(nameField().value).toBe('Tower');
    expect(shared?.state).toEqual(signedIn());

    // Saved again, it is a new project of the account's, not a revision of the trial's.
    api.save.mockResolvedValue({ kind: 'saved', project: saved('account-project') });
    fireEvent.click(projects().getByText('Save project'));
    await waitFor(() => {
      expect(api.save).toHaveBeenCalledTimes(2);
    });
    expect(api.save.mock.calls[1]?.[0].token).toBe(ACCOUNT_TOKEN);
    expect(api.revise).not.toHaveBeenCalled();
  });

  it('a signup claims this browser’s trial on the server, keeping the project open', async () => {
    window.localStorage.setItem(
      TRIAL_STORAGE_KEY,
      JSON.stringify({ sessionId: 's1', claimSecret: 'claim-secret' }),
    );
    api.save.mockResolvedValue({ kind: 'saved', project: saved() });
    // Signup joins the trial's tenant, so the new token names the same one.
    const claimed = jwt('user-2', 'trial-tenant');
    const signupImpl = vi.fn<typeof signupClaimingTrial>().mockResolvedValue({
      kind: 'signed-up',
      accessToken: claimed,
    });
    renderScreen({ signupImpl });
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    await screen.findByTestId('project-opened');

    fireEvent.click(within(screen.getByTestId('design-trial-mode')).getByText('Create an account'));
    const modal = within(await screen.findByTestId('trial-limit-modal'));
    expect(modal.getByText(/saved projects move into the new account/)).toBeTruthy();
    type(modal.getByLabelText('Email'), 'new@example.com');
    type(modal.getByLabelText('Password'), 'a long password');
    fireEvent.click(modal.getByRole('button', { name: 'Create account' }));

    await screen.findByTestId('design-account-mode');
    expect(signupImpl.mock.calls[0]?.[0].trial).toEqual({
      sessionId: 's1',
      claimSecret: 'claim-secret',
    });
    // Same tenant: the project open is the account's now.
    expect(screen.getByTestId('project-opened')).toBeTruthy();
    expect(screen.queryByTestId('design-account-trial-project-stays')).toBeNull();
    expect(nameField().value).toBe('Tower');
    // The claim secret is spent.
    expect(window.localStorage.getItem(TRIAL_STORAGE_KEY)).toBeNull();
  });
});

describe('when the session ends', () => {
  it('asks to sign in again, keeps the work, and never saves into a trial', async () => {
    api.save.mockResolvedValue({ kind: 'unauthorized' });
    const signInImpl = vi.fn<typeof signIn>().mockResolvedValue({
      kind: 'signed-in',
      accessToken: jwt('user-1', 'account-tenant', 'again'),
    });
    const { acquireImpl, refreshImpl } = renderScreen({ initial: signedIn(), signInImpl });
    await completeForm();
    type(overview().getByLabelText('Customer'), 'Typed before expiry');
    fireEvent.click(projects().getByText('Save project'));

    expect(await screen.findByTestId('design-account-expired')).toBeTruthy();
    expect(refreshImpl).toHaveBeenCalled();
    expect(acquireImpl).not.toHaveBeenCalled();
    expect(screen.queryByTestId('design-trial-mode')).toBeNull();
    expect(nameField().value).toBe('Tower');
    expect(overview().getByLabelText<HTMLInputElement>('Customer').value).toBe(
      'Typed before expiry',
    );
    // No token is held: nothing can be saved until the account signs in again.
    expect(screen.queryByTestId('projects-panel')).toBeNull();

    await signInThroughForm('again');
    api.save.mockResolvedValue({ kind: 'saved', project: saved() });
    fireEvent.click(projects().getByText('Save project'));
    await waitFor(() => {
      expect(api.save).toHaveBeenCalledTimes(2);
    });
    expect(api.save.mock.calls[1]?.[0].token).toBe(jwt('user-1', 'account-tenant', 'again'));
    expect(overview().getByLabelText<HTMLInputElement>('Customer').value).toBe(
      'Typed before expiry',
    );
    expect(acquireImpl).not.toHaveBeenCalled();
  });

  it('renews the token when it can, and the next save uses the renewed one', async () => {
    const renewed = jwt('user-1', 'account-tenant', 'renewed');
    const refreshImpl = vi
      .fn<typeof refreshSession>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: renewed });
    api.save.mockResolvedValueOnce({ kind: 'unauthorized' });
    const { acquireImpl } = renderScreen({ initial: signedIn(), refreshImpl });
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    expect(await screen.findByTestId('design-account-renewed')).toBeTruthy();
    expect(screen.getByTestId('design-account-mode')).toBeTruthy();

    api.save.mockResolvedValue({ kind: 'saved', project: saved() });
    fireEvent.click(projects().getByText('Save project'));
    await waitFor(() => {
      expect(api.save).toHaveBeenCalledTimes(2);
    });
    expect(api.save.mock.calls[1]?.[0].token).toBe(renewed);
    expect(acquireImpl).not.toHaveBeenCalled();
  });
});

describe('signing out', () => {
  it('clears the session and the work, and starts a trial only when asked', async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: saved() });
    const acquireImpl = vi.fn<typeof acquireTrial>().mockResolvedValue(trialReady());
    renderScreen({ initial: signedIn(), acquireImpl });
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    await screen.findByTestId('project-opened');
    type(overview().getByLabelText('Customer'), 'Unsaved');
    await waitFor(() => {
      expect(storedDraft()).not.toBeNull();
    });

    fireEvent.click(within(screen.getByTestId('design-account-mode')).getByText('Sign out'));

    expect(await screen.findByTestId('design-signed-out')).toBeTruthy();
    expect(shared?.state).toEqual({ status: 'signed-out' });
    expect(nameField().value).toBe('');
    expect(screen.queryByTestId('project-opened')).toBeNull();
    expect(screen.queryByTestId('projects-panel')).toBeNull();
    expect(storedDraft()).toBeNull();
    expect(acquireImpl).not.toHaveBeenCalled();

    fireEvent.click(
      within(screen.getByTestId('design-signed-out')).getByText('Continue as a trial'),
    );
    expect(await screen.findByTestId('design-trial-mode')).toBeTruthy();
    expect(acquireImpl).toHaveBeenCalledTimes(1);
  });
});

describe('the approval', () => {
  it('names the approver by the account the server resolved, never a typed name', () => {
    const project = saved();
    const [first] = project.revisions;
    if (!first) throw new Error('no revision');
    // A legacy row: the server sends the fallback, never the name once typed.
    first.approved_by = 'a team member';
    first.approved_at = '2026-10-03T10:00:00Z';
    expect(approvalOf(project)).toEqual({ by: null, at: '2026-10-03T10:00:00Z', note: null });
    first.approver_account_recorded = true;
    first.approver_name = 'Rana Haddad';
    first.approved_by = 'Rana Haddad';
    first.approval_note = 'R. Engineer';
    expect(approvalOf(project)).toEqual({
      by: 'Rana Haddad',
      at: '2026-10-03T10:00:00Z',
      note: 'R. Engineer',
    });
  });

  const panel = (
    by: string | null,
    options: { trial?: boolean; locale?: 'en' | 'ar' | 'he' } = {},
  ) =>
    renderApp(
      <ProjectsPanel
        token="t"
        current={null}
        opened={{
          id: 'p1',
          name: 'Tower',
          revision: 1,
          revisions: [1],
          approval: { by, at: '2026-10-03T10:00:00Z', note: 'R. Engineer' },
          approvingAs: 'Rana Haddad',
        }}
        onOpened={vi.fn()}
        trial={options.trial ?? false}
        impls={{ list: api.list }}
      />,
      options.locale ? { locale: options.locale } : {},
    );

  it('shows the approving account by its display name, never an email', async () => {
    panel('Rana Haddad');
    const line = await screen.findByTestId('project-approval');
    expect(line.textContent).toBe('Revision 1 approved by Rana Haddad on 2026-10-03.');
    expect(line.textContent).not.toContain('@');
    // The note is shown as a note, never as the approver.
    expect(screen.getByTestId('project-approval-note').textContent).toBe(
      'Approval note: R. Engineer',
    );
  });

  it('says a team member when the account has no display name', async () => {
    panel(null);
    expect((await screen.findByTestId('project-approval')).textContent).toBe(
      'Revision 1 approved by a team member on 2026-10-03.',
    );
  });
});

describe('approving in a trial', () => {
  const trialPanel = (locale: 'en' | 'ar' | 'he') =>
    renderApp(
      <ProjectsPanel
        token="t"
        current={null}
        opened={{
          id: 'p1',
          name: 'Tower',
          revision: 1,
          revisions: [1],
          approval: null,
          approvingAs: null,
        }}
        onOpened={vi.fn()}
        trial
        impls={{ list: api.list }}
      />,
      { locale },
    );

  it.each([
    ['en', 'Approving a revision needs a signed-in account. Sign in to approve; a trial cannot.'],
    [
      'ar',
      'اعتماد نسخة يتطلب حساباً مسجَّل الدخول. سجّل الدخول للاعتماد؛ لا تستطيع النسخة التجريبية ذلك.',
    ],
    ['he', 'אישור גרסה דורש חשבון מחובר. יש להתחבר כדי לאשר; ניסיון אינו יכול לאשר.'],
  ] as const)('is unavailable, with the reason, in %s', async (locale, reason) => {
    trialPanel(locale);
    expect((await screen.findByTestId('approve-held-trial')).textContent).toContain(reason);
    const button = screen
      .getAllByRole('button')
      .find((element) => element.getAttribute('aria-describedby')?.endsWith('-approve-held'));
    expect(button).toBeDefined();
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId('approving-as')).toBeNull();
  });

  it('reads the server refusal in every language', async () => {
    const { MESSAGES } = await import('./helpers');
    for (const locale of ['en', 'ar', 'he'] as const) {
      const errors = (MESSAGES[locale] as { design: { errors: Record<string, string> } }).design
        .errors;
      expect(errors.approval_requires_account).toBeTruthy();
    }
  });
});

describe('the shared session', () => {
  it('exchanges the refresh token once for several 401s at a time', async () => {
    let finish: (value: Awaited<ReturnType<typeof refreshSession>>) => void = () => undefined;
    const refreshImpl = vi.fn<typeof refreshSession>().mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    renderApp(
      <AccountSessionProvider initial={signedIn()} refreshImpl={refreshImpl}>
        <Probe />
      </AccountSessionProvider>,
    );
    const session = shared;
    if (session === null) throw new Error('no session');
    const first = session.renew();
    const second = session.renew();
    await act(async () => {
      finish({ kind: 'refreshed', accessToken: 'new' });
      await Promise.all([first, second]);
    });
    expect(refreshImpl).toHaveBeenCalledTimes(1);
    expect(await first).toBe('refreshed');
    expect(await second).toBe('refreshed');
    expect(shared?.state).toEqual({
      status: 'signed-in',
      tokens: { accessToken: 'new' },
    });
  });

  it('reads whom a token is for, and nothing from a token that is not one', () => {
    expect(tokenIdentity(ACCOUNT_TOKEN)).toEqual({ subject: 'user-1', tenant: 'account-tenant' });
    expect(tokenIdentity('opaque')).toBeNull();
    expect(tokenIdentity('a.!!!.b')).toBeNull();
  });
});

describe('whose work is on the page', () => {
  /** Save, have the session end, and land on the inline sign-in form. */
  async function expireWhileSaving(signInImpl: ReturnType<typeof vi.fn<typeof signIn>>) {
    api.save.mockResolvedValue({ kind: 'unauthorized' });
    const rendered = renderScreen({ initial: signedIn(), signInImpl });
    await completeForm();
    type(overview().getByLabelText('Customer'), 'Typed for user 1');
    type(overview().getByLabelText('Revision note'), 'expiry test');
    fireEvent.click(projects().getByText('Save project'));
    await screen.findByTestId('design-account-expired');
    return rendered;
  }

  it('keeps the revision note when the same account signs in again after its session ended', async () => {
    const again = jwt('user-1', 'account-tenant', 'again');
    const signInImpl = vi
      .fn<typeof signIn>()
      .mockResolvedValue({ kind: 'signed-in', accessToken: again });
    await expireWhileSaving(signInImpl);
    await signInThroughForm(again);

    expect(overview().getByLabelText<HTMLInputElement>('Revision note').value).toBe('expiry test');
    api.save.mockResolvedValue({ kind: 'saved', project: saved() });
    fireEvent.click(projects().getByText('Save project'));
    await waitFor(() => {
      expect(api.save).toHaveBeenCalledTimes(2);
    });
    expect(api.save.mock.calls[1]?.[0]).toMatchObject({ token: again, note: 'expiry test' });
    expect(screen.queryByTestId('design-account-account-switched')).toBeNull();
  });

  it('clears one account’s unsaved work when a different account signs in', async () => {
    const other = jwt('user-2', 'other-tenant');
    const signInImpl = vi
      .fn<typeof signIn>()
      .mockResolvedValue({ kind: 'signed-in', accessToken: other });
    await expireWhileSaving(signInImpl);
    await signInThroughForm(other);

    expect(await screen.findByTestId('design-account-account-switched')).toBeTruthy();
    expect(nameField().value).toBe('');
    expect(overview().getByLabelText<HTMLInputElement>('Customer').value).toBe('');
    expect(overview().getByLabelText<HTMLInputElement>('Revision note').value).toBe('');
    expect(screen.queryByTestId('project-opened')).toBeNull();
    expect(window.sessionStorage.getItem(DRAFT_STORAGE_KEY) ?? '').not.toContain(
      'Typed for user 1',
    );
  });

  it('clears the last save’s status when the page moves to another identity', async () => {
    api.save.mockResolvedValue({ kind: 'saved', project: saved('trial-project') });
    const signInImpl = vi.fn<typeof signIn>().mockResolvedValue({
      kind: 'signed-in',
      accessToken: ACCOUNT_TOKEN,
    });
    renderScreen({ signInImpl });
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    expect(await screen.findByText('Saved as revision 1.')).toBeTruthy();

    fireEvent.click(
      within(screen.getByTestId('design-trial-mode')).getByText('Sign in to save to your account'),
    );
    await signInThroughForm(ACCOUNT_TOKEN);
    await screen.findByTestId('design-account-trial-project-stays');
    expect(screen.queryByText('Saved as revision 1.')).toBeNull();
  });

  it('ignores a save started for the trial that answers after signing in', async () => {
    let answer: (outcome: Awaited<ReturnType<typeof saveProject>>) => void = () => undefined;
    api.save.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    const signInImpl = vi.fn<typeof signIn>().mockResolvedValue({
      kind: 'signed-in',
      accessToken: ACCOUNT_TOKEN,
    });
    renderScreen({ signInImpl });
    await completeForm();
    fireEvent.click(projects().getByText('Save project'));
    await waitFor(() => {
      expect(api.save).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(
      within(screen.getByTestId('design-trial-mode')).getByText('Sign in to save to your account'),
    );
    await signInThroughForm(ACCOUNT_TOKEN);
    await act(async () => {
      answer({ kind: 'saved', project: saved('trial-project') });
      await Promise.resolve();
    });

    // The trial's project is not opened under the account, nor said to be saved there.
    expect(screen.queryByTestId('project-opened')).toBeNull();
    expect(screen.queryByText('Saved as revision 1.')).toBeNull();
    expect(nameField().value).toBe('Tower');
  });
});
