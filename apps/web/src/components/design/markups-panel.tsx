'use client';

import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { useOutcomeText } from '@/components/design/note-text';
import { FilePicker } from '@/components/file-picker';
import {
  readMarkups,
  type DesignProject,
  type MarkupReport,
  type MarkupSuggestion,
} from '@/lib/design';

/**
 * A reviewed drawing set's marks: upload the PDF the consultant returned and
 * see every comment with the board and label it sits on. Where a comment on
 * a circuit asks for a change the schedule holds (a length, a power, a
 * removal), it is offered with an Apply button; nothing changes until the
 * engineer applies it, and the design is then run again by them.
 */
export function MarkupsPanel({
  token,
  project,
  profile,
  onApply,
  readImpl = readMarkups,
}: {
  token: string;
  project: DesignProject;
  profile: Record<string, unknown> | null;
  /** Apply a suggestion to the form; false when its circuit is no longer there. */
  onApply?: (suggestion: MarkupSuggestion) => boolean;
  readImpl?: typeof readMarkups;
}) {
  const t = useTranslations('design.markups');
  const say = useOutcomeText();
  const id = useId();
  const [report, setReport] = useState<MarkupReport | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  // Each suggestion's outcome, by its mark's place in the report.
  const [applied, setApplied] = useState<Record<number, 'applied' | 'missing'>>({});

  async function read(file: File) {
    setWorking(true);
    setMessage(null);
    const outcome = await readImpl({ token, file, filename: file.name, project, profile });
    setWorking(false);
    setApplied({});
    if (outcome.kind === 'read') setReport(outcome.report);
    else {
      setReport(null);
      setMessage(say(outcome));
    }
  }

  return (
    <section className="card flex flex-col gap-4 p-4 md:p-5" data-testid="markups-panel">
      <h2 className="text-lg font-semibold">{t('title')}</h2>
      <FilePicker
        id={`${id}-pdf`}
        label={t('upload')}
        help={t('help')}
        accept=".pdf,application/pdf"
        disabled={working}
        onFile={(file) => {
          void read(file);
        }}
      />
      {message && (
        <p role="status" className="text-sm" data-testid="markups-message">
          {message}
        </p>
      )}
      {report && (
        <div className="flex flex-col gap-2" data-testid="markups-result">
          <p className="text-sm text-text-muted">
            {report.markups.length === 0
              ? t('none')
              : t(report.matched ? 'found' : 'foundUnmatched', { count: report.markups.length })}
          </p>
          {report.markups.length > 0 && (
            <ol className="flex flex-col divide-y divide-border-subtle text-sm">
              {report.markups.map((markup, index) => (
                <li key={index} className="flex flex-col gap-1 py-2">
                  <span className="text-text-muted" dir="auto">
                    {[
                      t('page', { page: markup.page }),
                      markup.board,
                      markup.near && t('near', { label: markup.near }),
                      markup.author,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                  <span dir="auto">{markup.text}</span>
                  {markup.suggestion && onApply && (
                    <div
                      className="flex flex-wrap items-center gap-2"
                      data-testid={`markup-suggestion-${String(index)}`}
                    >
                      <span className="font-medium" dir="auto">
                        {t(`suggest.${markup.suggestion.field}`, {
                          circuit: markup.suggestion.circuit,
                          board: markup.suggestion.board,
                          value: markup.suggestion.value ?? '',
                        })}
                      </span>
                      {applied[index] ? (
                        <span className="text-text-muted">{t(applied[index])}</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            const suggestion = markup.suggestion;
                            if (!suggestion) return;
                            setApplied((current) => ({
                              ...current,
                              [index]: onApply(suggestion) ? 'applied' : 'missing',
                            }));
                          }}
                          className="btn btn-sm btn-secondary"
                        >
                          {t('apply')}
                        </button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
