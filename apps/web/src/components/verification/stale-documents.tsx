'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { useLocale } from '@/components/locale-provider';

type StaleDocument = components['schemas']['StaleDocument'];

/**
 * Live documents whose source changed or withdrew them since they were verified.
 *
 * Flags, not retractions: every passage here is still being served. A
 * reviewer reads the source as it is now and decides, with a note either way:
 * dismiss (the change does not matter) or retract (take the document's
 * passages out of live answers now). The note is required -- a decision
 * nobody can explain later is indistinguishable from one made to empty a list.
 *
 * Retraction asks for confirmation first. It is the one action on this page
 * that changes what engineers are told, immediately.
 */
export function StaleDocuments({
  items,
  onDismiss,
  onRetract,
}: {
  items: StaleDocument[];
  /**
   * Rejects with the server's reason when the dismissal is refused. The list
   * is the caller's: it removes a dismissed flag, so the item stays gone when
   * this component is remounted and the count beside the tab stays true.
   */
  onDismiss: (id: string, note: string) => Promise<void>;
  /** The same contract, for a retraction. */
  onRetract: (id: string, note: string) => Promise<void>;
}) {
  const t = useTranslations('stale');

  if (items.length === 0) {
    return (
      <p data-testid="stale-empty" className="card p-6 text-center text-sm text-text-muted">
        {t('empty')}
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-4" data-testid="stale-list">
      {items.map((item) => (
        <li key={item.id}>
          <StaleCard
            item={item}
            onDismiss={(note) => onDismiss(item.id, note)}
            onRetract={(note) => onRetract(item.id, note)}
          />
        </li>
      ))}
    </ul>
  );
}

function StaleCard({
  item,
  onDismiss,
  onRetract,
}: {
  item: StaleDocument;
  onDismiss: (note: string) => Promise<void>;
  onRetract: (note: string) => Promise<void>;
}) {
  const t = useTranslations('stale');
  const { locale } = useLocale();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<'dismiss' | 'retract' | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const noteId = useId();
  const warningId = useId();
  const withdrawn = item.reason === 'withdrawn';
  const noted = note.trim() !== '';

  async function decide(action: 'dismiss' | 'retract') {
    setBusy(action);
    setError(null);
    try {
      await (action === 'dismiss' ? onDismiss : onRetract)(note.trim());
    } catch (exc) {
      setError(
        exc instanceof Error && exc.message
          ? exc.message
          : t(action === 'dismiss' ? 'dismissFailed' : 'retractFailed'),
      );
      setBusy(null);
      setConfirming(false);
    }
  }

  return (
    <article
      data-testid={`stale-${item.id}`}
      className={`card border-s-4 p-4 md:p-5 ${
        withdrawn ? 'border-s-severity-critical' : 'border-s-severity-warning'
      }`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            withdrawn
              ? 'bg-severity-critical-surface text-severity-critical'
              : 'bg-severity-warning-surface text-severity-warning'
          }`}
        >
          {withdrawn ? t('withdrawn') : t('superseded')}
        </span>
        <span className="text-xs text-text-muted">
          {(t.raw('since') as string).replace('{date}', formatDay(item.first_flagged_at, locale))}
        </span>
      </div>
      {/* LTR whatever the page direction: a URL reordered by the bidi
          algorithm is a different URL to anyone copying it by eye. */}
      <a
        href={item.source_url}
        target="_blank"
        rel="noreferrer"
        dir="ltr"
        className="link block break-all font-mono text-sm"
      >
        {item.source_url}
      </a>
      <p className="mt-2 text-sm text-text-muted">
        {withdrawn ? t('withdrawnHelp') : t('supersededHelp')}
      </p>

      <form
        className="mt-4 flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void decide('dismiss');
        }}
      >
        <label htmlFor={noteId} className="text-sm font-medium text-text">
          {t('noteLabel')}
        </label>
        <textarea
          id={noteId}
          rows={2}
          value={note}
          onChange={(event) => {
            setNote(event.target.value);
          }}
          placeholder={t('notePlaceholder')}
          className="input font-sans text-sm"
        />
        {error !== null && (
          <p role="alert" className="text-sm text-severity-critical">
            {error}
          </p>
        )}
        {confirming ? (
          <div
            role="group"
            aria-describedby={warningId}
            className="flex flex-col gap-3 rounded-md border border-severity-critical bg-severity-critical-surface p-3"
          >
            <p id={warningId} className="text-sm text-severity-critical">
              {t('retractWarning')}
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy !== null || !noted}
                onClick={() => void decide('retract')}
                className="btn btn-sm btn-danger"
              >
                {busy === 'retract' ? t('retracting') : t('confirmRetract')}
              </button>
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => {
                  setConfirming(false);
                }}
                className="btn btn-sm btn-ghost"
              >
                {t('cancel')}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={busy !== null || !noted}
              className="btn btn-sm btn-secondary"
            >
              {busy === 'dismiss' ? t('dismissing') : t('dismiss')}
            </button>
            <button
              type="button"
              disabled={busy !== null || !noted}
              onClick={() => {
                setConfirming(true);
              }}
              className="btn btn-sm btn-danger"
            >
              {t('retract')}
            </button>
          </div>
        )}
      </form>
    </article>
  );
}

/** A date in the app's locale; empty rather than `Invalid Date` if unparseable. */
function formatDay(iso: string, locale: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' });
}
