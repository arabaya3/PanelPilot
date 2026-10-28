'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { useAccountSession } from '@/components/account-session';
import { AppShell } from '@/components/app-shell';
import { PageHeader, Panel, StatusBadge } from '@/components/ui';
import { SignInForm } from '@/components/sign-in-form';
import {
  getTeam,
  invite,
  joinLink,
  listInvitations,
  removeMember,
  revokeInvitation,
  transferOwnership,
  type InvitationOut,
  type Team,
  type TeamRefusal,
} from '@/lib/team';

type Loaded =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; team: Team; invitations: InvitationOut[] }
  | { kind: 'failed' };

/**
 * `/team`: who is on the account, and inviting colleagues into it.
 *
 * Signed-in accounts only: a trial has no team. The owner (the account's
 * first engineer) invites by email and gets a link to send, shown once;
 * the colleague opens it, signs up and joins. Once billing is enforced the
 * plan's seats bound how many.
 *
 * The token is the app's shared account session (`AccountSessionProvider`,
 * restored from the session cookie on every load), as on `/design`; a
 * sign-in here signs the whole app in. `initialToken` stands in only where
 * no session is shared (tests).
 */
export function TeamScreen({
  teamImpl = getTeam,
  inviteImpl = invite,
  invitationsImpl = listInvitations,
  revokeImpl = revokeInvitation,
  removeImpl = removeMember,
  transferImpl = transferOwnership,
  initialToken = null,
}: {
  teamImpl?: typeof getTeam;
  inviteImpl?: typeof invite;
  invitationsImpl?: typeof listInvitations;
  revokeImpl?: typeof revokeInvitation;
  removeImpl?: typeof removeMember;
  transferImpl?: typeof transferOwnership;
  initialToken?: string | null;
}) {
  const t = useTranslations('team');
  const id = useId();
  const account = useAccountSession();
  const [ownToken, setOwnToken] = useState<string | null>(initialToken);
  const token = account?.state.status === 'signed-in' ? account.state.tokens.accessToken : ownToken;
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'idle' });
  // The token a renewal was already asked for, so a server that refuses the
  // renewed token too is said as an error rather than renewed in a loop.
  const renewedFor = useRef<string | null>(null);

  /**
   * After a 401: renew the shared session, as `/design` does. A renewed
   * token reloads the team (the token changes); one that cannot be renewed
   * ends the session, and the page asks for a sign-in. True when renewed.
   */
  const reauthorize = useCallback(
    async (access: string): Promise<boolean> => {
      if (account === null || renewedFor.current === access) return false;
      renewedFor.current = access;
      return (await account.renew()) === 'refreshed';
    },
    [account],
  );
  const [email, setEmail] = useState('');
  const [link, setLink] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // The member a transfer awaits confirmation for: ownership is not undone by the caller.
  const [transferTo, setTransferTo] = useState<string | null>(null);

  const say = useCallback(
    (outcome: { kind: string }) => {
      if (outcome.kind === 'refused') {
        const { code, params } = outcome as TeamRefusal;
        if (code && t.has(`errors.${code}`)) return t(`errors.${code}`, params);
      }
      return t('error');
    },
    [t],
  );

  const load = useCallback(
    async (access: string) => {
      setLoaded({ kind: 'loading' });
      const shown = await teamImpl({ token: access });
      if (shown.kind === 'unauthorized' && (await reauthorize(access))) return;
      if (shown.kind !== 'team') {
        setLoaded({ kind: 'failed' });
        return;
      }
      const open = shown.team.owner ? await invitationsImpl({ token: access }) : null;
      setLoaded({
        kind: 'ready',
        team: shown.team,
        invitations: open?.kind === 'listed' ? open.invitations : [],
      });
    },
    [teamImpl, invitationsImpl, reauthorize],
  );

  useEffect(() => {
    if (token !== null) void load(token);
  }, [token, load]);

  async function sendInvite() {
    if (token === null) return;
    setMessage(null);
    setLink(null);
    const outcome = await inviteImpl({ token, email: email.trim() });
    if (outcome.kind === 'invited') {
      setLink(joinLink(window.location.origin, outcome.invitation.token));
      setEmail('');
      void load(token);
    } else if (outcome.kind === 'unauthorized' && (await reauthorize(token))) {
      setMessage(t('renewed'));
    } else {
      setMessage(say(outcome));
    }
  }

  async function act(run: () => Promise<{ kind: string }>) {
    if (token === null) return;
    setMessage(null);
    const outcome = await run();
    if (outcome.kind === 'done') void load(token);
    else if (outcome.kind === 'unauthorized' && (await reauthorize(token)))
      setMessage(t('renewed'));
    else setMessage(say(outcome));
  }

  async function transfer(memberId: string) {
    if (token === null) return;
    setMessage(null);
    setTransferTo(null);
    const outcome = await transferImpl({ token, memberId });
    if (outcome.kind === 'team') {
      // The team as the caller now sees it: no longer the owner, so no invitations.
      setLoaded({ kind: 'ready', team: outcome.team, invitations: [] });
    } else if (outcome.kind === 'unauthorized' && (await reauthorize(token))) {
      setMessage(t('renewed'));
    } else {
      setMessage(say(outcome));
    }
  }

  return (
    <AppShell>
      <PageHeader title={t('title')} description={t('intro')} />

      {token === null && (
        <div className="panel max-w-md p-4">
          <p className="mb-3 text-sm">{t('signIn')}</p>
          <SignInForm
            onSignedIn={(tokens) => {
              if (account) account.signedIn(tokens);
              else setOwnToken(tokens.accessToken);
            }}
            onCancel={() => undefined}
          />
        </div>
      )}

      {/* Signed out (the session ended): only the sign-in, not what the old token left. */}
      {token !== null && loaded.kind === 'loading' && (
        <p className="text-text-muted">{t('loading')}</p>
      )}
      {token !== null && loaded.kind === 'failed' && (
        <p role="alert" className="text-severity-critical">
          {t('error')}
        </p>
      )}

      {loaded.kind === 'ready' && token !== null && (
        <div className="flex max-w-3xl flex-col gap-5">
          <p className="text-sm text-text-muted" data-testid="team-seats">
            {loaded.team.seats === null
              ? t('seatsOpen', { taken: loaded.team.seats_taken })
              : t('seats', { taken: loaded.team.seats_taken, seats: loaded.team.seats })}
          </p>

          <Panel title={t('members')} bodyClassName="p-0">
            <ul className="flex flex-col" data-testid="team-members">
              {loaded.team.members.map((member) => (
                <li
                  key={member.id}
                  className="flex flex-wrap items-center gap-3 border-b border-border-subtle px-4 py-2 last:border-b-0"
                >
                  <span className="min-w-0 flex-1 break-words" dir="ltr">
                    {member.full_name ? `${member.full_name} · ${member.email}` : member.email}
                  </span>
                  {member.owner && <StatusBadge tone="accent">{t('owner')}</StatusBadge>}
                  {loaded.team.owns && !member.owner && transferTo !== member.id && (
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      onClick={() => {
                        setMessage(null);
                        setTransferTo(member.id);
                      }}
                    >
                      {t('makeOwner')}
                    </button>
                  )}
                  {loaded.team.owner && !member.owner && (
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      onClick={() => void act(() => removeImpl({ token, id: member.id }))}
                    >
                      {t('remove')}
                    </button>
                  )}
                  {loaded.team.owns && transferTo === member.id && (
                    <div
                      className="flex w-full flex-wrap items-center gap-2 text-sm"
                      data-testid="team-transfer-confirm"
                    >
                      <p className="min-w-0 flex-1 break-words">
                        {t('transferConfirm', { member: member.full_name ?? member.email })}{' '}
                        <span dir="ltr">({member.email})</span>
                      </p>
                      <button
                        type="button"
                        className="btn btn-sm btn-primary"
                        onClick={() => void transfer(member.id)}
                      >
                        {t('transfer')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-secondary"
                        onClick={() => {
                          setTransferTo(null);
                        }}
                      >
                        {t('cancel')}
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </Panel>

          {loaded.team.owner ? (
            <Panel title={t('invite')}>
              <form
                className="flex flex-wrap items-end gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void sendInvite();
                }}
              >
                <label className="field field-label max-w-full" htmlFor={`${id}-email`}>
                  {t('email')}
                  <input
                    id={`${id}-email`}
                    type="email"
                    dir="ltr"
                    required
                    value={email}
                    onChange={(event) => {
                      setEmail(event.target.value);
                    }}
                    className="input w-64 max-w-full"
                  />
                </label>
                <button type="submit" className="btn btn-primary" disabled={email.trim() === ''}>
                  {t('send')}
                </button>
              </form>
              {link && (
                <div className="mt-3 rounded-md border border-border-subtle bg-surface-raised p-3 text-sm">
                  <p className="mb-1">{t('linkReady')}</p>
                  <code dir="ltr" className="break-all" data-testid="team-link">
                    {link}
                  </code>
                </div>
              )}
              {loaded.invitations.length > 0 && (
                <ul className="mt-3 flex flex-col gap-2" data-testid="team-invitations">
                  {loaded.invitations.map((open) => (
                    <li key={open.id} className="flex items-center gap-3 text-sm">
                      <span className="flex-1" dir="ltr">
                        {open.email}
                      </span>
                      <StatusBadge tone="warning">{t('waiting')}</StatusBadge>
                      <button
                        type="button"
                        className="btn btn-sm btn-secondary"
                        onClick={() => void act(() => revokeImpl({ token, id: open.id }))}
                      >
                        {t('withdraw')}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          ) : (
            <p className="text-sm text-text-muted">{t('notOwner')}</p>
          )}

          {message && (
            <p role="alert" className="text-sm text-severity-critical">
              {message}
            </p>
          )}
        </div>
      )}
    </AppShell>
  );
}
