'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useCallback, useState, type SyntheticEvent } from 'react';

import { LangSwitcher } from '@/components/lang-switcher';
import { ThemeToggle } from '@/components/theme-toggle';
import { VerificationConsole, type VerificationLabels } from '@/components/verification';
import {
  loadQueue,
  signIn,
  tokenIsReviewer,
  verificationApi,
  type QueueItem,
} from '@/lib/verification';

/**
 * The reviewers' page (FE-012's console, mounted).
 *
 * A reviewer's `correct` label publishes content to production, so this page
 * needs a real account — not the anonymous trial — holding the reviewer role.
 * The token lives only in this component's state.
 */
type Phase =
  | { kind: 'signed-out'; error: 'rejected' | 'failed' | null; busy: boolean }
  | { kind: 'not-reviewer' }
  | { kind: 'load-failed'; token: string }
  | { kind: 'reviewing'; token: string; items: QueueItem[] };

export default function VerificationPage() {
  const t = useTranslations('verification');
  const [phase, setPhase] = useState<Phase>({ kind: 'signed-out', error: null, busy: false });
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const load = useCallback(async (token: string) => {
    const queue = await loadQueue(token);
    if (queue.kind === 'loaded') setPhase({ kind: 'reviewing', token, items: queue.items });
    else if (queue.kind === 'forbidden') setPhase({ kind: 'not-reviewer' });
    else setPhase({ kind: 'load-failed', token });
  }, []);

  const submit = useCallback(
    async (event: SyntheticEvent<HTMLFormElement>) => {
      event.preventDefault();
      setPhase({ kind: 'signed-out', error: null, busy: true });
      const outcome = await signIn(email, password);
      if (outcome.kind !== 'signed-in') {
        setPhase({ kind: 'signed-out', error: outcome.kind, busy: false });
        return;
      }
      setPassword('');
      if (!tokenIsReviewer(outcome.token)) {
        setPhase({ kind: 'not-reviewer' });
        return;
      }
      await load(outcome.token);
    },
    [email, load, password],
  );

  const labels: VerificationLabels = {
    heading: t('heading'),
    empty: t('empty'),
    itemCount: t.raw('itemCount') as string,
    proposed: t('proposed'),
    source: t('source'),
    sourceMissing: t('sourceMissing'),
    correct: t('correct'),
    incorrect: t('incorrect'),
    uncertain: t('uncertain'),
    notePlaceholder: t('notePlaceholder'),
    noteRequired: t('noteRequired'),
    submit: t('submit'),
    submitting: t('submitting'),
    claimedBy: t.raw('claimedBy') as string,
    submitFailed: t('submitFailed'),
    contentMissing: t('contentMissing'),
    citation: t.raw('citation') as string,
  };

  return (
    <main className="min-h-screen bg-bg p-6 text-text">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl">{t('heading')}</h1>
        <div className="flex flex-wrap items-center gap-4">
          <Link className="text-accent hover:text-accent-hover" href="/">
            PanelPilot
          </Link>
          {phase.kind !== 'signed-out' && (
            <button
              type="button"
              className="text-sm text-accent hover:text-accent-hover"
              onClick={() => {
                setPhase({ kind: 'signed-out', error: null, busy: false });
              }}
            >
              {t('signOut')}
            </button>
          )}
          <LangSwitcher />
          <ThemeToggle />
        </div>
      </div>

      {phase.kind === 'signed-out' && (
        <form
          onSubmit={(event) => {
            void submit(event);
          }}
          className="flex max-w-sm flex-col gap-3"
          aria-labelledby="verification-sign-in"
        >
          <h2 id="verification-sign-in" className="text-lg font-semibold">
            {t('signInHeading')}
          </h2>
          <label className="flex flex-col gap-1 text-sm">
            {t('email')}
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
              }}
              className="rounded-md border border-border bg-surface p-2 text-text"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t('password')}
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              className="rounded-md border border-border bg-surface p-2 text-text"
            />
          </label>
          {phase.error !== null && (
            <p role="alert" className="text-sm text-severity-critical">
              {phase.error === 'rejected' ? t('signInFailed') : t('loadFailed')}
            </p>
          )}
          <button
            type="submit"
            disabled={phase.busy}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-accent-contrast disabled:opacity-50"
          >
            {phase.busy ? t('signingIn') : t('signIn')}
          </button>
        </form>
      )}

      {phase.kind === 'not-reviewer' && (
        <p
          role="alert"
          className="max-w-2xl rounded-md border border-severity-warning bg-severity-warning-surface p-3 text-sm text-text"
        >
          {t('notReviewer')}
        </p>
      )}

      {phase.kind === 'load-failed' && (
        <p role="alert" className="text-sm text-severity-critical">
          {t('loadFailed')}
        </p>
      )}

      {phase.kind === 'reviewing' && (
        <VerificationConsole
          items={phase.items}
          api={verificationApi(phase.token)}
          sourceUrlFor={(item) => item.source_url ?? null}
          labels={labels}
        />
      )}
    </main>
  );
}
