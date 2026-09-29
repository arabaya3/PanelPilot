'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useState } from 'react';
import type { components } from '@panelpilot/shared-types';

import { AppShell } from '@/components/app-shell';
import { CheckCircleIcon } from '@/components/icons';
import { SignInForm } from '@/components/sign-in-form';
import type { signIn } from '@/lib/auth';
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
    <AppShell>
      {phase.kind === 'signed-out' && (
        <div className="mx-auto flex w-full max-w-md flex-col items-center gap-5 py-6 text-center">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-subtle text-accent-hover">
            <CheckCircleIcon width="24" height="24" />
          </span>
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-bold tracking-tight">{t('heading')}</h1>
            <p className="text-text-muted">{t('intro')}</p>
          </div>
          <div className="flex w-full justify-center text-start">
            <SignInForm
              onSignedIn={(tokens) => {
                void load(tokens.accessToken);
              }}
              {...(signInImpl ? { signInImpl } : {})}
            />
          </div>
        </div>
      )}

      {phase.kind === 'loading' && (
        <div className="card flex items-center justify-center gap-3 p-7">
          <span
            aria-hidden="true"
            className="h-4 w-4 animate-spin rounded-full border-2 border-border-subtle border-t-accent"
          />
          <p data-testid="review-loading" className="text-sm text-text-muted">
            {t('loading')}
          </p>
        </div>
      )}

      {phase.kind === 'forbidden' && (
        <div
          role="alert"
          data-testid="review-forbidden"
          className="mx-auto flex w-full max-w-lg flex-col items-start gap-3 rounded-lg border border-severity-warning bg-severity-warning-surface p-4"
        >
          <p className="text-sm text-severity-warning">{t('forbidden')}</p>
          <button
            type="button"
            onClick={() => {
              setPhase({ kind: 'signed-out' });
            }}
            className="btn btn-sm btn-secondary"
          >
            {t('otherAccount')}
          </button>
        </div>
      )}

      {phase.kind === 'failed' && (
        <div
          role="alert"
          data-testid="review-failed"
          className="mx-auto flex w-full max-w-lg flex-col items-start gap-3 rounded-lg border border-severity-critical bg-severity-critical-surface p-4"
        >
          <p className="text-sm text-severity-critical">{t('failed')}</p>
          <button
            type="button"
            onClick={() => {
              void load(phase.token);
            }}
            className="btn btn-sm btn-primary"
          >
            {t('retry')}
          </button>
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
    </AppShell>
  );
}
