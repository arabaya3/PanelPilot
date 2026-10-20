'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

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
 */
export function TeamScreen({
  teamImpl = getTeam,
  inviteImpl = invite,
  invitationsImpl = listInvitations,
  revokeImpl = revokeInvitation,
  removeImpl = removeMember,
  initialToken = null,
}: {
  teamImpl?: typeof getTeam;
  inviteImpl?: typeof invite;
  invitationsImpl?: typeof listInvitations;
  revokeImpl?: typeof revokeInvitation;
  removeImpl?: typeof removeMember;
  initialToken?: string | null;
}) {
  const t = useTranslations('team');
  const id = useId();
  const [token, setToken] = useState<string | null>(initialToken);
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'idle' });
  const [email, setEmail] = useState('');
  const [link, setLink] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

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
    [teamImpl, invitationsImpl],
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
    } else {
      setMessage(say(outcome));
    }
  }

  async function act(run: () => Promise<{ kind: string }>) {
    if (token === null) return;
    setMessage(null);
    const outcome = await run();
    if (outcome.kind === 'done') void load(token);
    else setMessage(say(outcome));
  }

  return (
    <AppShell>
      <PageHeader title={t('title')} description={t('intro')} />

      {token === null && (
        <div className="panel max-w-md p-4">
          <p className="mb-3 text-sm">{t('signIn')}</p>
          <SignInForm
            onSignedIn={(tokens) => {
              setToken(tokens.accessToken);
            }}
            onCancel={() => undefined}
          />
        </div>
      )}

      {loaded.kind === 'loading' && <p className="text-text-muted">{t('loading')}</p>}
      {loaded.kind === 'failed' && (
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
                  {loaded.team.owner && !member.owner && (
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      onClick={() => void act(() => removeImpl({ token, id: member.id }))}
                    >
                      {t('remove')}
                    </button>
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
