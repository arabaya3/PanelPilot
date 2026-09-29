'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useCallback, useState } from 'react';
import type { components } from '@panelpilot/shared-types';

import { LangSwitcher } from '@/components/lang-switcher';
import { SignInForm } from '@/components/sign-in-form';
import type { signIn } from '@/lib/auth';
import { ThemeToggle } from '@/components/theme-toggle';
import { VerificationConsole, type VerificationLabels } from '@/components/verification';
import {
  fetchQueue,
  sourceUrlFor,
  submitLabel as submitLabelRequest,
  type QueueOutcome,
} from '@/lib/verification';

type QueueItem = components['schemas']['QueueItem'];

type Phase =
  | { kind: 'signed-out' }
  | { kind: 'loading'; token: string }
  | { kind: 'loaded'; token: string; items: QueueItem[] }
  | { kind: 'forbidden' }
  | { kind: 'failed'; token: string };

/**
 * `/review`: a reviewer signs in and works their queue.
 *
 * The console existed with no page around it, so the ten engineers the
 * promotion gate depends on had nowhere to label anything. The token lives in
 * this component's memory only, as on the home page.
 */
export function ReviewScreen({
  fetchQueueImpl = fetchQueue,
  submitLabelImpl = submitLabelRequest,
  signInImpl,
}: {
  fetchQueueImpl?: typeof fetchQueue;
  submitLabelImpl?: typeof submitLabelRequest;
  signInImpl?: typeof signIn;
}) {
  const t = useTranslations('review');
  const tApp = useTranslations('app');
  const tv = useTranslations('verification');
  const [phase, setPhase] = useState<Phase>({ kind: 'signed-out' });

  const load = useCallback(
    async (token: string) => {
      setPhase({ kind: 'loading', token });
      const outcome: QueueOutcome = await fetchQueueImpl({ token });
      if (outcome.kind === 'loaded') setPhase({ kind: 'loaded', token, items: outcome.items });
      else if (outcome.kind === 'forbidden') setPhase({ kind: 'forbidden' });
      else if (outcome.kind === 'unauthorized') setPhase({ kind: 'signed-out' });
      else setPhase({ kind: 'failed', token });
    },
    [fetchQueueImpl],
  );

  // `raw` for the three with placeholders: the console fills `{count}`,
  // `{page}` and `{name}` itself, and formatting them here without values
  // made next-intl fall back to printing the message key.
  const labels: VerificationLabels = {
    heading: tv('heading'),
    empty: tv('empty'),
    itemCount: tv.raw('itemCount') as string,
    proposed: tv('proposed'),
    source: tv('source'),
    sourceMissing: tv('sourceMissing'),
    contentMissing: tv('contentMissing'),
    openSource: tv('openSource'),
    page: tv.raw('page') as string,
    correct: tv('correct'),
    incorrect: tv('incorrect'),
    uncertain: tv('uncertain'),
    notePlaceholder: tv('notePlaceholder'),
    noteRequired: tv('noteRequired'),
    submit: tv('submit'),
    submitting: tv('submitting'),
    claimedBy: tv.raw('claimedBy') as string,
    submitFailed: tv('submitFailed'),
  };

  return (
    <main className="min-h-screen bg-bg p-6 text-text">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl">
          <Link href="/" className="hover:text-accent-hover">
            {tApp('name')}
          </Link>
        </h1>
        <div className="flex flex-wrap items-center gap-4">
          <LangSwitcher />
          <ThemeToggle />
        </div>
      </div>

      {phase.kind === 'signed-out' && (
        <div className="flex flex-col gap-4">
          <p className="max-w-2xl text-text-muted">{t('intro')}</p>
          <SignInForm
            onSignedIn={(tokens) => {
              void load(tokens.accessToken);
            }}
            {...(signInImpl ? { signInImpl } : {})}
          />
        </div>
      )}

      {phase.kind === 'loading' && (
        <p data-testid="review-loading" className="text-sm text-text-muted">
          {t('loading')}
        </p>
      )}

      {phase.kind === 'forbidden' && (
        <div role="alert" data-testid="review-forbidden" className="flex max-w-2xl flex-col gap-2">
          <p className="text-sm text-severity-warning">{t('forbidden')}</p>
          <div>
            <button
              type="button"
              onClick={() => {
                setPhase({ kind: 'signed-out' });
              }}
              className="rounded-md border border-border px-3 py-2 text-sm text-text"
            >
              {t('otherAccount')}
            </button>
          </div>
        </div>
      )}

      {phase.kind === 'failed' && (
        <div role="alert" data-testid="review-failed" className="flex flex-col gap-2">
          <p className="text-sm text-severity-critical">{t('failed')}</p>
          <div>
            <button
              type="button"
              onClick={() => {
                void load(phase.token);
              }}
              className="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-accent-contrast"
            >
              {t('retry')}
            </button>
          </div>
        </div>
      )}

      {phase.kind === 'loaded' && (
        <VerificationConsole
          items={phase.items}
          sourceUrlFor={sourceUrlFor}
          labels={labels}
          api={{
            submitLabel: (itemId, label, note) =>
              submitLabelImpl({ token: phase.token, itemId, label, note }),
          }}
        />
      )}
    </main>
  );
}
