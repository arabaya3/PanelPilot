import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { JoinScreen } from '@/components/join-screen';
import { TeamScreen } from '@/components/team-screen';
import type {
  getTeam,
  invite,
  listInvitations,
  removeMember,
  revokeInvitation,
  signupWithInvite,
  Team,
} from '@/lib/team';
import { joinLink } from '@/lib/team';

import { renderApp } from './helpers';

/** `/team` and `/join`: inviting colleagues into an account. */

const TEAM: Team = {
  members: [
    { id: 'o', email: 'owner@x.com', full_name: 'Owner', owner: true },
    { id: 'm', email: 'mem@x.com', full_name: null, owner: false },
  ],
  owner: true,
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
