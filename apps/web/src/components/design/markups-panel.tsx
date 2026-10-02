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
                    <SuggestionRow
                      index={index}
                      suggestion={markup.suggestion}
                      outcome={applied[index]}
                      onApply={(chosen) => {
                        // Applied outside the state update: it changes the form's state.
                        const outcome = onApply(chosen) ? 'applied' : 'missing';
                        setApplied((current) => ({ ...current, [index]: outcome }));
                      }}
                    />
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

/**
 * One comment's suggested change and its Apply button. A comment on a
 * residual current group of several circuits names no circuit: the engineer
 * picks the one it means first.
 */
function SuggestionRow({
  index,
  suggestion,
  outcome,
  onApply,
}: {
  index: number;
  suggestion: MarkupSuggestion;
  outcome: 'applied' | 'missing' | undefined;
  onApply: (chosen: MarkupSuggestion) => void;
}) {
  const t = useTranslations('design.markups');
  const id = useId();
  const candidates = suggestion.candidates ?? [];
  const [picked, setPicked] = useState<number | null>(null);
  const choice = picked === null ? undefined : candidates[picked];
  const chosen: MarkupSuggestion | null =
    candidates.length === 0
      ? suggestion
      : choice
        ? { ...suggestion, circuit: choice.circuit, load_index: choice.load_index, candidates: [] }
        : null;
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      data-testid={`markup-suggestion-${String(index)}`}
    >
      <span className="font-medium" dir="auto">
        {t(`suggest.${suggestion.field}`, {
          circuit: chosen?.circuit ?? t('whichCircuit'),
          board: suggestion.board,
          value: suggestion.value ?? '',
        })}
      </span>
      {candidates.length > 0 && !outcome && (
        <>
          <label htmlFor={`${id}-pick`} className="sr-only">
            {t('whichCircuit')}
          </label>
          <select
            id={`${id}-pick`}
            value={picked ?? ''}
            onChange={(event) => {
              setPicked(event.target.value === '' ? null : Number(event.target.value));
            }}
            className="input"
          >
            <option value="">{t('whichCircuit')}</option>
            {candidates.map((candidate, place) => (
              <option key={candidate.load_index} value={place}>
                {candidate.circuit}
              </option>
            ))}
          </select>
        </>
      )}
      {outcome ? (
        <span className="text-text-muted">{t(outcome)}</span>
      ) : (
        <button
          type="button"
          disabled={chosen === null}
          onClick={() => {
            if (chosen) onApply(chosen);
          }}
          className="btn btn-sm btn-secondary"
        >
          {t('apply')}
        </button>
      )}
    </div>
  );
}
