'use client';

import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { MAX_REASON_LENGTH, type FlagOutcome } from '@/lib/feedback';

/**
 * "This answer is wrong", under an answer.
 *
 * Closed by default and small: most answers are not reported, and a form
 * open under every card would crowd the steps an engineer is working through.
 * The reason is optional -- a report with no words still queues the answer
 * for a reviewer, and asking for an essay is how reports stop being made.
 */
export function ReportAnswer({
  sent,
  onReport,
}: {
  /** Already reported, so the thanks shows instead of the button. */
  sent: boolean;
  onReport: (reason: string) => Promise<FlagOutcome>;
}) {
  const t = useTranslations('report');
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Exclude<FlagOutcome['kind'], 'sent'> | null>(null);
  const fieldId = useId();

  if (sent) {
    return (
      <p role="status" className="mt-2 text-sm text-text-muted" data-testid="report-sent">
        {t('sent')}
      </p>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn btn-sm btn-ghost mt-2"
        data-testid="report-open"
        onClick={() => {
          setOpen(true);
        }}
      >
        {t('open')}
      </button>
    );
  }

  return (
    <form
      className="card mt-2 flex flex-col gap-2 p-3"
      data-testid="report-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (pending) return;
        setPending(true);
        setError(null);
        void onReport(reason).then((outcome) => {
          setPending(false);
          // On success the parent flips `sent`; nothing to do here.
          if (outcome.kind !== 'sent') {
            setError(outcome.kind);
          }
        });
      }}
    >
      <label htmlFor={fieldId} className="text-sm font-medium text-text">
        {t('reasonLabel')}
      </label>
      <textarea
        id={fieldId}
        className="input"
        rows={3}
        dir="auto"
        value={reason}
        maxLength={MAX_REASON_LENGTH}
        placeholder={t('reasonPlaceholder')}
        onChange={(event) => {
          setReason(event.target.value);
        }}
      />
      {error ? (
        <p role="alert" className="text-sm text-severity-critical" data-testid="report-error">
          {t(`error.${error}`)}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <button type="submit" className="btn btn-sm btn-primary" disabled={pending}>
          {pending ? t('sending') : t('send')}
        </button>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={() => {
            setOpen(false);
            setError(null);
          }}
        >
          {t('cancel')}
        </button>
      </div>
    </form>
  );
}
