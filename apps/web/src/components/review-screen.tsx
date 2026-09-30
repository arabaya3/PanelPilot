'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useId, useState } from 'react';
import type { components } from '@panelpilot/shared-types';

import { AppShell } from '@/components/app-shell';
import { CheckCircleIcon } from '@/components/icons';
import { SignInForm } from '@/components/sign-in-form';
import type { signIn } from '@/lib/auth';
import { VerificationConsole, type VerificationLabels } from '@/components/verification';
import { Escalations } from '@/components/verification/escalations';
import { StaleDocuments } from '@/components/verification/stale-documents';
import {
  dismissStale,
  fetchEscalations,
  fetchQueue,
  fetchStale,
  resolveEscalation,
  retractStale,
  sourceUrlFor,
  submitLabel as submitLabelRequest,
  type QueueOutcome,
  type StaleOutcome,
} from '@/lib/verification';

type QueueItem = components['schemas']['QueueItem'];

type Phase =
  | { kind: 'signed-out' }
  | { kind: 'loading'; token: string }
  | {
      kind: 'loaded';
      token: string;
      items: QueueItem[];
      stale: StaleOutcome;
      escalations: QueueOutcome;
    }
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
  fetchStaleImpl = fetchStale,
  dismissStaleImpl = dismissStale,
  retractStaleImpl = retractStale,
  fetchEscalationsImpl = fetchEscalations,
  resolveEscalationImpl = resolveEscalation,
  signInImpl,
}: {
  fetchQueueImpl?: typeof fetchQueue;
  submitLabelImpl?: typeof submitLabelRequest;
  fetchStaleImpl?: typeof fetchStale;
  dismissStaleImpl?: typeof dismissStale;
  retractStaleImpl?: typeof retractStale;
  fetchEscalationsImpl?: typeof fetchEscalations;
  resolveEscalationImpl?: typeof resolveEscalation;
  signInImpl?: typeof signIn;
}) {
  const t = useTranslations('review');
  const tv = useTranslations('verification');
  const [phase, setPhase] = useState<Phase>({ kind: 'signed-out' });
  const [tab, setTab] = useState<'queue' | 'stale' | 'escalations'>('queue');

  // A decided flag leaves the open list, whichever way it was decided. The
  // list lives here rather than in the component that shows it, so it stays
  // true across a tab switch and in the count beside the tab.
  const removeFlag = useCallback((id: string) => {
    setPhase((current) =>
      current.kind === 'loaded' && current.stale.kind === 'loaded'
        ? {
            ...current,
            stale: {
              ...current.stale,
              items: current.stale.items.filter((item) => item.id !== id),
            },
          }
        : current,
    );
  }, []);
  const removeEscalation = useCallback((id: string) => {
    setPhase((current) =>
      current.kind === 'loaded' && current.escalations.kind === 'loaded'
        ? {
            ...current,
            escalations: {
              ...current.escalations,
              items: current.escalations.items.filter((item) => item.id !== id),
            },
          }
        : current,
    );
  }, []);
  const tabsId = useId();

  const load = useCallback(
    async (token: string) => {
      setPhase({ kind: 'loading', token });
      // Together, not one after the other: the flags are a second list on
      // the same page, and waiting for the queue first only delays both.
      const [outcome, stale, escalations]: [QueueOutcome, StaleOutcome, QueueOutcome] =
        await Promise.all([
          fetchQueueImpl({ token }),
          fetchStaleImpl({ token }),
          fetchEscalationsImpl({ token }),
        ]);
      if (outcome.kind === 'loaded')
        setPhase({ kind: 'loaded', token, items: outcome.items, stale, escalations });
      else if (outcome.kind === 'forbidden') setPhase({ kind: 'forbidden' });
      else if (outcome.kind === 'unauthorized') setPhase({ kind: 'signed-out' });
      else setPhase({ kind: 'failed', token });
    },
    [fetchQueueImpl, fetchStaleImpl, fetchEscalationsImpl],
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
    reported: tv('reported'),
    reportedQuestion: tv('reportedQuestion'),
    reportedAnswer: tv('reportedAnswer'),
    reportedReason: tv('reportedReason'),
    reportedNoReason: tv('reportedNoReason'),
    reportedPassages: tv('reportedPassages'),
    reportedNoPassages: tv('reportedNoPassages'),
    reportedContextMissing: tv('reportedContextMissing'),
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
        <div className="flex flex-col gap-5">
          {/* The page's one h1 once signed in; the signed-out view has its own. */}
          <h1 className="text-2xl font-bold tracking-tight">{t('heading')}</h1>
          <div
            role="tablist"
            aria-label={t('heading')}
            className="flex gap-1 self-start rounded-lg border border-border-subtle bg-surface p-1"
          >
            {(['queue', 'stale', 'escalations'] as const).map((key) => {
              const list = key === 'stale' ? phase.stale : phase.escalations;
              const count =
                key === 'queue'
                  ? phase.items.length
                  : list.kind === 'loaded'
                    ? list.items.length
                    : null;
              const selected = tab === key;
              return (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  id={`${tabsId}-${key}`}
                  aria-selected={selected}
                  aria-controls={`${tabsId}-${key}-panel`}
                  data-testid={`review-tab-${key}`}
                  onClick={() => {
                    setTab(key);
                  }}
                  className={`inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
                    selected
                      ? 'bg-accent-subtle font-semibold text-accent-hover'
                      : 'font-medium text-text-muted hover:bg-surface-raised hover:text-text'
                  }`}
                >
                  {key === 'queue'
                    ? t('tabQueue')
                    : key === 'stale'
                      ? t('tabStale')
                      : t('tabEscalations')}
                  {count !== null && (
                    <span className="rounded-full bg-surface-raised px-2 text-xs text-text">
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div role="tabpanel" id={`${tabsId}-${tab}-panel`} aria-labelledby={`${tabsId}-${tab}`}>
            {tab === 'queue' ? (
              <VerificationConsole
                items={phase.items}
                sourceUrlFor={sourceUrlFor}
                labels={labels}
                api={{
                  submitLabel: (itemId, label, note) =>
                    submitLabelImpl({ token: phase.token, itemId, label, note }),
                }}
              />
            ) : tab === 'escalations' ? (
              phase.escalations.kind === 'loaded' ? (
                <Escalations
                  items={phase.escalations.items}
                  labels={labels}
                  sourceUrlFor={sourceUrlFor}
                  onResolve={async (id, outcome, note) => {
                    await resolveEscalationImpl({ token: phase.token, id, outcome, note });
                    removeEscalation(id);
                    // Taken over lands in this lead's own queue.
                    if (outcome === 'taken-over') {
                      const refreshed = await fetchQueueImpl({ token: phase.token });
                      if (refreshed.kind === 'loaded') {
                        setPhase((current) =>
                          current.kind === 'loaded'
                            ? { ...current, items: refreshed.items }
                            : current,
                        );
                      }
                    }
                  }}
                />
              ) : (
                <p
                  role="alert"
                  data-testid="escalations-failed"
                  className="rounded-lg border border-severity-critical bg-severity-critical-surface p-4 text-sm text-severity-critical"
                >
                  {t('escalationsFailed')}
                </p>
              )
            ) : phase.stale.kind === 'loaded' ? (
              <StaleDocuments
                items={phase.stale.items}
                onDismiss={async (id, note) => {
                  await dismissStaleImpl({ token: phase.token, id, note });
                  removeFlag(id);
                }}
                onRetract={async (id, note) => {
                  await retractStaleImpl({ token: phase.token, id, note });
                  removeFlag(id);
                }}
              />
            ) : (
              <p
                role="alert"
                data-testid="stale-failed"
                className="rounded-lg border border-severity-critical bg-severity-critical-surface p-4 text-sm text-severity-critical"
              >
                {t('staleFailed')}
              </p>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}
