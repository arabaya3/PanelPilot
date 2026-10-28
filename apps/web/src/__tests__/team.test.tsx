import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AccountSessionProvider } from '@/components/account-session';
import { JoinScreen } from '@/components/join-screen';
import { TeamScreen } from '@/components/team-screen';
import type { refreshSession } from '@/lib/auth';
import type {
  getTeam,
  invite,
  listInvitations,
  removeMember,
  revokeInvitation,
  signupWithInvite,
  Team,
  transferOwnership,
} from '@/lib/team';
import { joinLink, transferOwnership as realTransfer } from '@/lib/team';

import { renderApp } from './helpers';

/** `/team` and `/join`: inviting colleagues into an account. */

const TEAM: Team = {
  members: [
    { id: 'o', email: 'owner@x.com', full_name: 'Owner', owner: true },
    { id: 'm', email: 'mem@x.com', full_name: null, owner: false },
  ],
  owner: true,
  owns: true,
  seats: 3,
  seats_taken: 3,
};

function screenWith(overrides: Partial<Parameters<typeof TeamScreen>[0]> = {}) {
  const teamImpl = vi.fn<typeof getTeam>().mockResolvedValue({ kind: 'team', team: TEAM });
  const invitationsImpl = vi.fn<typeof listInvitations>().mockResolvedValue({
    kind: 'listed',
    invitations: [
      { id: 'i', email: 'new@x.com', invited_by: 'owner@x.com', expires_at: '2026-10-18' },
    ],
  });
  renderApp(
    <TeamScreen
      initialToken="tok"
      teamImpl={teamImpl}
      invitationsImpl={invitationsImpl}
      {...overrides}
    />,
  );
  return { teamImpl, invitationsImpl };
}

describe('team', () => {
  it('makes the link a colleague opens', () => {
    expect(joinLink('https://app.example', 'a b')).toBe('https://app.example/join?invite=a%20b');
  });

  it('shows the members, seats and open invitations to the owner', async () => {
    screenWith();
    expect((await screen.findByTestId('team-members')).textContent).toContain('mem@x.com');
    expect(screen.getByTestId('team-seats').textContent).toContain('3 of 3');
    expect(screen.getByTestId('team-invitations').textContent).toContain('new@x.com');
  });

  it('invites a colleague and shows the link once', async () => {
    const inviteImpl = vi.fn<typeof invite>().mockResolvedValue({
      kind: 'invited',
      invitation: { id: 'n', email: 'c@x.com', token: 'secret', expires_at: '2026-10-18' },
    });
    screenWith({ inviteImpl });
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'c@x.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create invitation' }));
    expect((await screen.findByTestId('team-link')).textContent).toContain('/join?invite=secret');
    expect(inviteImpl).toHaveBeenCalledWith({ token: 'tok', email: 'c@x.com' });
  });

  it('says a full plan in the reader language', async () => {
    const inviteImpl = vi.fn<typeof invite>().mockResolvedValue({
      kind: 'refused',
      code: 'team_seats_full',
      params: { seats: '3' },
    });
    screenWith({ inviteImpl });
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'c@x.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create invitation' }));
    expect((await screen.findByRole('alert')).textContent).toContain('holds 3 seats');
  });

  it('removes a member and withdraws an invitation', async () => {
    const removeImpl = vi.fn<typeof removeMember>().mockResolvedValue({ kind: 'done' });
    const revokeImpl = vi.fn<typeof revokeInvitation>().mockResolvedValue({ kind: 'done' });
    const { teamImpl } = screenWith({ removeImpl, revokeImpl });
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    await waitFor(() => {
      expect(removeImpl).toHaveBeenCalledWith({ token: 'tok', id: 'm' });
    });
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw' }));
    await waitFor(() => {
      expect(revokeImpl).toHaveBeenCalledWith({ token: 'tok', id: 'i' });
    });
    expect(teamImpl.mock.calls.length).toBeGreaterThan(1);
  });

  it('asks a visitor without an account to sign in', () => {
    renderApp(<TeamScreen />);
    expect(screen.getByText('Sign in to see your team.')).toBeTruthy();
  });
});

describe('join', () => {
  it('creates the account with the invitation', async () => {
    const signupImpl = vi.fn<typeof signupWithInvite>().mockResolvedValue({ kind: 'joined' });
    renderApp(<JoinScreen initialInvite="tok" signupImpl={signupImpl} />);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'c@x.com' } });
    fireEvent.change(screen.getByLabelText('Full name'), { target: { value: 'Colleague' } });
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: 'a-long-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create my account' }));
    expect((await screen.findByRole('status')).textContent).toContain('in your team');
    expect(signupImpl).toHaveBeenCalledWith({
      email: 'c@x.com',
      password: 'a-long-password',
      fullName: 'Colleague',
      inviteToken: 'tok',
    });
  });

  it('says an invitation for another address', async () => {
    const signupImpl = vi.fn<typeof signupWithInvite>().mockResolvedValue({
      kind: 'refused',
      code: 'team_invitation_email',
      params: {},
    });
    renderApp(<JoinScreen initialInvite="tok" signupImpl={signupImpl} />);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'c@x.com' } });
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: 'a-long-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create my account' }));
    expect((await screen.findByRole('alert')).textContent).toContain('another email');
  });

  it('needs an invitation', () => {
    renderApp(<JoinScreen initialInvite="" />);
    expect(screen.getByText(/needs an invitation link/)).toBeTruthy();
  });
});

describe('team under the shared account session', () => {
  function stubs() {
    return {
      teamImpl: vi.fn<typeof getTeam>().mockResolvedValue({ kind: 'team', team: TEAM }),
      invitationsImpl: vi
        .fn<typeof listInvitations>()
        .mockResolvedValue({ kind: 'listed', invitations: [] }),
    };
  }

  it('uses the session restored from the cookie on load, not a sign-in of its own', async () => {
    const { teamImpl, invitationsImpl } = stubs();
    const refreshImpl = vi
      .fn<typeof refreshSession>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: 'restored' });
    renderApp(
      <AccountSessionProvider restore refreshImpl={refreshImpl}>
        <TeamScreen teamImpl={teamImpl} invitationsImpl={invitationsImpl} />
      </AccountSessionProvider>,
    );
    expect((await screen.findByTestId('team-members')).textContent).toContain('mem@x.com');
    expect(teamImpl).toHaveBeenCalledWith({ token: 'restored' });
    expect(screen.queryByText('Sign in to see your team.')).toBeNull();
  });

  it('asks for a sign-in only when the browser has no session', async () => {
    const { teamImpl, invitationsImpl } = stubs();
    const refreshImpl = vi.fn<typeof refreshSession>().mockResolvedValue({ kind: 'expired' });
    renderApp(
      <AccountSessionProvider restore refreshImpl={refreshImpl}>
        <TeamScreen teamImpl={teamImpl} invitationsImpl={invitationsImpl} />
      </AccountSessionProvider>,
    );
    expect(await screen.findByText('Sign in to see your team.')).toBeTruthy();
    expect(teamImpl).not.toHaveBeenCalled();
  });
});

describe('joining with an address the server refuses', () => {
  it('says the email cannot be used, and keeps what was typed', async () => {
    const signupImpl = vi.fn<typeof signupWithInvite>().mockResolvedValue({
      kind: 'invalid',
      problems: [{ field: 'email' }],
    });
    renderApp(<JoinScreen initialInvite="tok" signupImpl={signupImpl} />);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'c@x.test' } });
    fireEvent.change(screen.getByLabelText(/^Password/), {
      target: { value: 'a-long-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create my account' }));
    expect((await screen.findByRole('alert')).textContent).toContain(
      'This email address cannot be used',
    );
    expect(screen.getByLabelText<HTMLInputElement>('Email').value).toBe('c@x.test');
  });
});

describe('team after the access token expires', () => {
  it('renews the shared session after a 401 and loads with the new token', async () => {
    const teamImpl = vi
      .fn<typeof getTeam>()
      .mockImplementation(({ token }) =>
        Promise.resolve(
          token === 'renewed' ? { kind: 'team', team: TEAM } : { kind: 'unauthorized' },
        ),
      );
    const invitationsImpl = vi
      .fn<typeof listInvitations>()
      .mockResolvedValue({ kind: 'listed', invitations: [] });
    const refreshImpl = vi
      .fn<typeof refreshSession>()
      .mockResolvedValue({ kind: 'refreshed', accessToken: 'renewed' });
    renderApp(
      <AccountSessionProvider
        initial={{ status: 'signed-in', tokens: { accessToken: 'stale' } }}
        refreshImpl={refreshImpl}
      >
        <TeamScreen teamImpl={teamImpl} invitationsImpl={invitationsImpl} />
      </AccountSessionProvider>,
    );
    expect((await screen.findByTestId('team-members')).textContent).toContain('mem@x.com');
    expect(refreshImpl).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('asks for a sign-in when the session cannot be renewed', async () => {
    const teamImpl = vi.fn<typeof getTeam>().mockResolvedValue({ kind: 'unauthorized' });
    const refreshImpl = vi.fn<typeof refreshSession>().mockResolvedValue({ kind: 'expired' });
    renderApp(
      <AccountSessionProvider
        initial={{ status: 'signed-in', tokens: { accessToken: 'stale' } }}
        refreshImpl={refreshImpl}
      >
        <TeamScreen teamImpl={teamImpl} />
      </AccountSessionProvider>,
    );
    expect(await screen.findByText('Sign in to see your team.')).toBeTruthy();
    expect(teamImpl).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.queryByRole('alert')).toBeNull();
    });
    expect(screen.queryByText('Loading the team…')).toBeNull();
  });
});

describe('transferring ownership', () => {
  it('offers "Make owner" only to the owner, for the other members', async () => {
    screenWith();
    await screen.findByTestId('team-members');
    expect(screen.getAllByRole('button', { name: 'Make owner' })).toHaveLength(1);
  });

  it('does not offer it to a manager who is not the owner', async () => {
    const teamImpl = vi
      .fn<typeof getTeam>()
      .mockResolvedValue({ kind: 'team', team: { ...TEAM, owns: false } });
    screenWith({ teamImpl });
    await screen.findByTestId('team-members');
    expect(screen.queryByRole('button', { name: 'Make owner' })).toBeNull();
  });

  it('asks before transferring, and cancelling sends nothing', async () => {
    const transferImpl = vi.fn<typeof transferOwnership>();
    screenWith({ transferImpl });
    fireEvent.click(await screen.findByRole('button', { name: 'Make owner' }));
    expect(screen.getByTestId('team-transfer-confirm').textContent).toContain('mem@x.com');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('team-transfer-confirm')).toBeNull();
    expect(transferImpl).not.toHaveBeenCalled();
  });

  it('transfers on confirmation and shows the team it returns', async () => {
    const after: Team = {
      ...TEAM,
      members: [
        { id: 'm', email: 'mem@x.com', full_name: null, owner: true },
        { id: 'o', email: 'owner@x.com', full_name: 'Owner', owner: false },
      ],
      owner: false,
      owns: false,
    };
    const transferImpl = vi
      .fn<typeof transferOwnership>()
      .mockResolvedValue({ kind: 'team', team: after });
    screenWith({ transferImpl });
    fireEvent.click(await screen.findByRole('button', { name: 'Make owner' }));
    fireEvent.click(screen.getByRole('button', { name: 'Transfer ownership' }));
    await waitFor(() => {
      expect(transferImpl).toHaveBeenCalledWith({ token: 'tok', memberId: 'm' });
    });
    expect(await screen.findByText("Only the account's owner invites colleagues.")).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Make owner' })).toBeNull();
  });

  it.each([
    ['team_transfer_owner_only', "Only the account's owner can transfer ownership."],
    ['team_transfer_self', 'You already own the account'],
    ['team_member_not_found', 'That member is not there.'],
  ])('says the refusal %s in the reader language', async (code, text) => {
    const transferImpl = vi
      .fn<typeof transferOwnership>()
      .mockResolvedValue({ kind: 'refused', code, params: {} });
    screenWith({ transferImpl });
    fireEvent.click(await screen.findByRole('button', { name: 'Make owner' }));
    fireEvent.click(screen.getByRole('button', { name: 'Transfer ownership' }));
    expect((await screen.findByRole('alert')).textContent).toContain(text);
  });

  it('posts the member to /api/v1/team/owner', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify(TEAM), { status: 200 }));
    const result = await realTransfer({ token: 'tok', memberId: 'm', fetchImpl });
    expect(result).toEqual({ kind: 'team', team: TEAM });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/team/owner');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBe(JSON.stringify({ member_id: 'm' }));
  });

  it('reads a 403 refusal code from the server', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ code: 'team_transfer_owner_only' }), { status: 403 }),
      );
    expect(await realTransfer({ token: 'tok', memberId: 'm', fetchImpl })).toEqual({
      kind: 'refused',
      code: 'team_transfer_owner_only',
      params: {},
    });
  });
});
