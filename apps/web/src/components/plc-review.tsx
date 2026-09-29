'use client';

import type { components } from '@panelpilot/shared-types';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { LangSwitcher } from '@/components/lang-switcher';
import { PlcView } from '@/components/plc-view';
import { ThemeToggle } from '@/components/theme-toggle';
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
  const tApp = useTranslations('app');
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

      <div className="max-w-3xl">
        <h2 className="mb-2 text-xl">{t('heading')}</h2>
        <p className="mb-4 text-text-muted">{t('intro')}</p>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void check();
          }}
          className="mb-6 flex flex-col gap-2"
        >
          <label htmlFor={fieldId} className="text-sm font-medium">
            {t('label')}
          </label>
          <textarea
            id={fieldId}
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
            }}
            rows={14}
            spellCheck={false}
            dir="ltr"
            placeholder={t('placeholder')}
            className="w-full rounded-md border border-border bg-surface p-3 font-mono text-sm text-text placeholder:text-text-muted"
          />
          <div>
            <button
              type="submit"
              disabled={source.trim() === '' || review.kind === 'checking'}
              className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-contrast disabled:opacity-50"
            >
              {review.kind === 'checking' ? t('checking') : t('check')}
            </button>
          </div>
        </form>

        {review.kind === 'reviewed' && (
          <PlcView language="structured-text" source={review.source} validation={review.result} />
        )}
        {review.kind === 'rejected' && (
          <p role="alert" data-testid="plc-rejected" className="text-sm text-severity-warning">
            {review.detail ?? t('rejected')}
          </p>
        )}
        {review.kind === 'failed' && (
          <p role="alert" data-testid="plc-failed" className="text-sm text-severity-critical">
            {t('failed')}
          </p>
        )}
      </div>
    </main>
  );
}
