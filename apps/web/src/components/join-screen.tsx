'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useEffect, useId, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import { useSignupProblemText } from '@/components/signup-problems';
import { Notice, PageHeader } from '@/components/ui';
import { signupWithInvite, type JoinOutcome } from '@/lib/team';

/**
 * `/join?invite=…`: a colleague creates their account in the team that
 * invited them. The invitation is for one email; the server checks it.
 */
export function JoinScreen({
  signupImpl = signupWithInvite,
  initialInvite,
}: {
  signupImpl?: typeof signupWithInvite;
  /** The invitation token; read from the address when not given. */
  initialInvite?: string;
}) {
  const t = useTranslations('join');
  const tTeam = useTranslations('team');
  const id = useId();
  const sayProblems = useSignupProblemText();
  const [inviteToken, setInviteToken] = useState(initialInvite ?? '');
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [outcome, setOutcome] = useState<JoinOutcome | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (initialInvite !== undefined) return;
    setInviteToken(new URLSearchParams(window.location.search).get('invite') ?? '');
  }, [initialInvite]);

  async function join() {
    setWorking(true);
    setOutcome(await signupImpl({ email: email.trim(), password, fullName, inviteToken }));
    setWorking(false);
  }

  function problem(result: JoinOutcome): string {
    if (result.kind === 'email-taken') return t('emailTaken');
    if (result.kind === 'invalid') return sayProblems(result.problems);
    if (result.kind === 'refused') {
      const { code, params } = result;
      if (code && tTeam.has(`errors.${code}`)) return tTeam(`errors.${code}`, params);
    }
    return t('failed');
  }

  return (
    <AppShell>
      <PageHeader title={t('title')} />
      {inviteToken === '' ? (
        <Notice tone="info" live={false}>
          {t('noInvite')}
        </Notice>
      ) : outcome?.kind === 'joined' ? (
        <p role="status" className="max-w-xl">
          {t('joined')}{' '}
          <Link href="/" className="link">
            {t('signIn')}
          </Link>
        </p>
      ) : (
        <form
          className="panel flex max-w-md flex-col gap-3 p-5"
          onSubmit={(event) => {
            event.preventDefault();
            void join();
          }}
        >
          <p className="text-sm text-text-muted">{t('intro')}</p>
          <label className="field field-label" htmlFor={`${id}-email`}>
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
              className="input w-full"
            />
          </label>
          <label className="field field-label" htmlFor={`${id}-name`}>
            {t('name')}
            <input
              id={`${id}-name`}
              value={fullName}
              onChange={(event) => {
                setFullName(event.target.value);
              }}
              className="input w-full"
            />
          </label>
          <label className="field field-label" htmlFor={`${id}-password`}>
            {t('password')}
            <input
              id={`${id}-password`}
              type="password"
              dir="ltr"
              required
              minLength={12}
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              className="input w-full"
            />
          </label>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={working || email.trim() === '' || password.length < 12}
          >
            {t('submit')}
          </button>
          {outcome && (
            <p role="alert" className="text-sm text-severity-critical">
              {problem(outcome)}
            </p>
          )}
        </form>
      )}
    </AppShell>
  );
}
