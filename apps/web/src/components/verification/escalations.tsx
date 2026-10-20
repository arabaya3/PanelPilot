'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { EmptyState, StatusBadge, TableFrame } from '@/components/ui';
import type { EscalationDecision } from '@/lib/verification';

import { ReportedAnswer, type VerificationLabels } from './index';

type QueueItem = components['schemas']['QueueItem'];

/**
 * Items a verifier would not pass, for a lead to settle.
 *
 * Two ways out, both with a note. **Uphold** agrees with the verifier: the
 * content stays out of live answers, or a reported answer is confirmed wrong.
 * **Take it over** moves the item into the lead's own queue to be labelled
 * afresh -- so publishing anything still goes through the one labelled,
 * checked path, whoever overrules whom.
 *
 * Whoever escalated an item cannot resolve it (AI-012). Their own
 * escalations are shown, but without the controls, and say why.
 */
export function Escalations({
  items,
  labels,
  sourceUrlFor,
  onResolve,
}: {
  items: QueueItem[];
  labels: VerificationLabels;
  sourceUrlFor: (item: QueueItem) => string | null;
  /**
   * Rejects with the server's reason when the resolution is refused. The
   * list is the caller's: it removes a resolved item.
   */
  onResolve: (id: string, outcome: EscalationDecision, note: string) => Promise<void>;
}) {
  const t = useTranslations('escalations');
  // The level under the page's h1, for a screen reader walking the
  // headings; the selected tab already says it visibly.
  const heading = <h2 className="sr-only">{t('heading')}</h2>;

  if (items.length === 0) {
    return (
      <>
        {heading}
        <div data-testid="escalations-empty">
          <EmptyState title={t('empty')} />
        </div>
      </>
    );
  }

  return (
    <>
      {heading}
      {/* Dense rows, one per item; the frame scrolls on a phone rather than
          squeezing the text. */}
      <TableFrame label={t('heading')}>
        <table className="data-table" data-testid="escalations-list">
          <thead>
            <tr>
              <th scope="col">{t('columns.verdict')}</th>
              <th scope="col">{t('verifierNote')}</th>
              <th scope="col">{t('columns.content')}</th>
              <th scope="col">{t('columns.decision')}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <EscalationRow
                key={item.id}
                item={item}
                labels={labels}
                sourceUrl={sourceUrlFor(item)}
                onResolve={(outcome, note) => onResolve(item.id, outcome, note)}
              />
            ))}
          </tbody>
        </table>
      </TableFrame>
    </>
  );
}

function EscalationRow({
  item,
  labels,
  sourceUrl,
  onResolve,
}: {
  item: QueueItem;
  labels: VerificationLabels;
  sourceUrl: string | null;
  onResolve: (outcome: EscalationDecision, note: string) => Promise<void>;
}) {
  const t = useTranslations('escalations');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<EscalationDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const noteId = useId();
  const noted = note.trim() !== '';
  const incorrect = item.label === 'incorrect';

  async function decide(outcome: EscalationDecision) {
    setBusy(outcome);
    setError(null);
    try {
      await onResolve(outcome, note.trim());
    } catch (exc) {
      setError(exc instanceof Error && exc.message ? exc.message : t('failed'));
      setBusy(null);
    }
  }

  return (
    <tr data-testid={`escalation-${item.id}`}>
      <td className="align-top">
        <div className="flex flex-col items-start gap-2">
          <StatusBadge tone={incorrect ? 'critical' : 'warning'}>
            {incorrect ? labels.incorrect : labels.uncertain}
          </StatusBadge>
          <span className="text-xs text-text-muted">
            {item.flag ? t('originFlag') : t('originCrawl')}
          </span>
        </div>
      </td>
      <td className="min-w-48 align-top">
        <p dir="auto" data-testid="escalation-note" className="text-sm text-text">
          {item.note ?? ''}
        </p>
      </td>
      <td className="min-w-64 max-w-md align-top">
        {item.flag ? (
          <ReportedAnswer flag={item.flag} labels={labels} />
        ) : item.content ? (
          <blockquote
            dir="auto"
            className="mb-3 max-h-96 overflow-y-auto whitespace-pre-wrap rounded-md border border-border-subtle bg-surface-raised p-3 text-sm leading-relaxed text-text"
          >
            {item.content}
          </blockquote>
        ) : (
          <p className="mb-3 text-sm text-severity-warning">{labels.contentMissing}</p>
        )}
        {sourceUrl !== null && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="link mb-3 inline-block text-sm"
          >
            {labels.openSource}
          </a>
        )}
      </td>
      <td className="min-w-64 align-top">
        {item.assigned_to_you ? (
          <p data-testid="escalation-yours" className="text-sm text-text-muted">
            {t('yours')}
          </p>
        ) : (
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void decide('upheld');
            }}
          >
            <label htmlFor={noteId} className="text-sm font-medium text-text">
              {t('noteLabel')}
            </label>
            <textarea
              id={noteId}
              rows={2}
              dir="auto"
              value={note}
              onChange={(event) => {
                setNote(event.target.value);
              }}
              className="input font-sans text-sm"
            />
            {error !== null && (
              <p role="alert" className="text-sm text-severity-critical">
                {error}
              </p>
            )}
            <p className="text-xs text-text-muted">{t('help')}</p>
            <div className="flex flex-wrap gap-2">
              <button
                type="submit"
                disabled={busy !== null || !noted}
                className="btn btn-sm btn-primary"
              >
                {busy === 'upheld' ? t('working') : t('uphold')}
              </button>
              <button
                type="button"
                disabled={busy !== null || !noted}
                onClick={() => void decide('taken-over')}
                className="btn btn-sm btn-secondary"
              >
                {busy === 'taken-over' ? t('working') : t('takeOver')}
              </button>
            </div>
          </form>
        )}
      </td>
    </tr>
  );
}
