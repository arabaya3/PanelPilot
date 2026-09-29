'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import { CheckCircleIcon, CodeIcon } from '@/components/icons';
import { PlcView } from '@/components/plc-view';
import { reviewPlc } from '@/lib/plc';

type PlcValidationResult = components['schemas']['PlcValidationResult'];

type Review =
  | { kind: 'idle' }
  | { kind: 'checking' }
  /** The source as submitted, so the lines match the findings' numbers. */
  | { kind: 'reviewed'; source: string; result: PlcValidationResult }
  | { kind: 'rejected'; detail: string | null }
  | { kind: 'failed' };

/**
 * Paste Structured Text, get it checked.
 *
 * The review endpoint and `PlcView` both existed with no page between them,
 * so the only way to reach either was the API directly. The findings are
 * drawn against the program exactly as it was submitted, not the text box's
 * current contents: an edit after checking would otherwise put line 8's error
 * beside whatever line 8 has become.
 */
export function PlcReview({ reviewImpl = reviewPlc }: { reviewImpl?: typeof reviewPlc }) {
  const t = useTranslations('plc');
  const [source, setSource] = useState('');
  const [review, setReview] = useState<Review>({ kind: 'idle' });
  const fieldId = useId();

  async function check() {
    const submitted = source;
    setReview({ kind: 'checking' });
    const outcome = await reviewImpl({ source: submitted });
    if (outcome.kind === 'reviewed') {
      setReview({ kind: 'reviewed', source: submitted, result: outcome.result });
    } else {
      setReview(outcome);
    }
  }

  return (
    <AppShell>
      <div className="mb-6 flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight">{t('heading')}</h1>
        <p className="max-w-3xl text-text-muted">{t('intro')}</p>
      </div>

      {/* Side by side from `lg`, so a finding and the line it names are both
          on screen; stacked below that, the code first. */}
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void check();
          }}
          className="card flex flex-col gap-3 p-4 md:p-5"
        >
          <label htmlFor={fieldId} className="text-sm font-semibold">
            {t('label')}
          </label>
          <textarea
            id={fieldId}
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
            }}
            rows={16}
            spellCheck={false}
            dir="ltr"
            placeholder={t('placeholder')}
            className="w-full rounded-md border border-border bg-surface-raised p-3 font-mono text-sm leading-relaxed text-text placeholder:text-text-muted"
          />
          <div>
            <button
              type="submit"
              disabled={source.trim() === '' || review.kind === 'checking'}
              className="btn btn-primary"
            >
              <CheckCircleIcon width="16" height="16" />
              {review.kind === 'checking' ? t('checking') : t('check')}
            </button>
          </div>
        </form>

        <div className="flex min-w-0 flex-col gap-3">
          {review.kind === 'reviewed' && (
            <PlcView language="structured-text" source={review.source} validation={review.result} />
          )}
          {review.kind === 'rejected' && (
            <p
              role="alert"
              data-testid="plc-rejected"
              className="rounded-lg border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
            >
              {review.detail ?? t('rejected')}
            </p>
          )}
          {review.kind === 'failed' && (
            <p
              role="alert"
              data-testid="plc-failed"
              className="rounded-lg border border-severity-critical bg-severity-critical-surface p-4 text-sm text-severity-critical"
            >
              {t('failed')}
            </p>
          )}
          {(review.kind === 'idle' || review.kind === 'checking') && (
            <div className="flex min-h-96 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-border-subtle p-6 text-center text-sm text-text-muted">
              {review.kind === 'checking' ? (
                <span
                  aria-hidden="true"
                  className="h-6 w-6 animate-spin rounded-full border-2 border-border-subtle border-t-accent"
                />
              ) : (
                <CodeIcon width="28" height="28" className="text-accent" />
              )}
              <p className="max-w-sm">{t('emptyResult')}</p>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
