'use client';

import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { useNoteText, useOutcomeText } from '@/components/design/note-text';
import { FilePicker } from '@/components/file-picker';
import {
  importSchedule,
  suggestSchedule,
  type DesignNote,
  type LoadScheduleImport,
} from '@/lib/design';

type State<T> =
  | { kind: 'idle' }
  | { kind: 'working' }
  | ({ kind: 'done' } & T)
  | { kind: 'error'; detail: string };

/**
 * Two ways to fill a board's schedule without typing it: describe the board
 * in words for the model to draft, or read the consultant's file. Either
 * replaces the rows of the board being edited, for the engineer to check.
 */
export function ScheduleSources({
  token,
  supplyPhases,
  profile,
  onLoads,
  onUnauthorized,
  importImpl = importSchedule,
  suggestImpl = suggestSchedule,
}: {
  token: string | null;
  supplyPhases: number;
  profile: Record<string, unknown> | null;
  onLoads: (loads: LoadScheduleImport['loads']) => void;
  onUnauthorized: () => void;
  importImpl?: typeof importSchedule;
  suggestImpl?: typeof suggestSchedule;
}) {
  const t = useTranslations('design');
  const noteText = useNoteText();
  const say = useOutcomeText();
  const id = useId();
  const [brief, setBrief] = useState('');
  const [suggestState, setSuggestState] = useState<State<{ assumptions: DesignNote[] }>>({
    kind: 'idle',
  });
  const [importState, setImportState] = useState<State<{ count: number; warnings: DesignNote[] }>>({
    kind: 'idle',
  });

  async function suggest() {
    if (token === null || brief.trim().length < 3) return;
    setSuggestState({ kind: 'working' });
    const outcome = await suggestImpl({
      token,
      description: brief.trim(),
      supplyPhases,
      profile,
    });
    if (outcome.kind === 'suggested') {
      onLoads(outcome.result.loads);
      setSuggestState({ kind: 'done', assumptions: outcome.result.assumptions });
      return;
    }
    setSuggestState({
      kind: 'error',
      detail: outcome.kind === 'budget' ? t('suggest.budget') : say(outcome),
    });
    if (outcome.kind === 'unauthorized') onUnauthorized();
  }

  async function importFile(file: File) {
    if (token === null) return;
    setImportState({ kind: 'working' });
    const outcome = await importImpl({ token, file, filename: file.name });
    if (outcome.kind === 'imported') {
      onLoads(outcome.result.loads);
      setImportState({
        kind: 'done',
        count: outcome.result.loads.length,
        warnings: outcome.result.warnings,
      });
      return;
    }
    setImportState({
      kind: 'error',
      detail: say(outcome),
    });
    if (outcome.kind === 'unauthorized') onUnauthorized();
  }

  return (
    <>
      <div className="flex flex-col gap-2">
        <label htmlFor={`${id}-brief`} className="text-sm font-semibold">
          {t('suggest.label')}
        </label>
        <textarea
          id={`${id}-brief`}
          rows={3}
          value={brief}
          placeholder={t('suggest.placeholder')}
          onChange={(event) => {
            setBrief(event.target.value);
          }}
          className="input w-full"
        />
        <p className="text-sm text-text-muted">{t('suggest.help')}</p>
        <button
          type="button"
          disabled={token === null || brief.trim().length < 3 || suggestState.kind === 'working'}
          onClick={() => {
            void suggest();
          }}
          className="btn btn-sm btn-secondary self-start"
        >
          {suggestState.kind === 'working' ? t('suggest.working') : t('suggest.submit')}
        </button>
        {suggestState.kind === 'error' && (
          <p role="alert" className="text-sm text-danger" data-testid="suggest-error">
            {suggestState.detail}
          </p>
        )}
        {suggestState.kind === 'done' && (
          <div data-testid="suggest-result" className="text-sm">
            <p>{t('suggest.done')}</p>
            <ul className="mt-1 list-disc ps-5 text-text-muted">
              {suggestState.assumptions.map((assumption, index) => (
                <li key={index}>{noteText(assumption)}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <div className="flex flex-col gap-2">
        <FilePicker
          id={`${id}-import`}
          label={t('import.label')}
          help={t('import.help')}
          accept=".xlsx,.csv,.pdf,application/pdf,text/csv"
          disabled={token === null || importState.kind === 'working'}
          onFile={(file) => {
            void importFile(file);
          }}
        />
        {importState.kind === 'working' && (
          <p className="text-sm text-text-muted">{t('import.working')}</p>
        )}
        {importState.kind === 'error' && (
          <p role="alert" className="text-sm text-danger" data-testid="import-error">
            {importState.detail}
          </p>
        )}
        {importState.kind === 'done' && (
          <div data-testid="import-result" className="text-sm">
            <p>{t('import.done', { count: importState.count })}</p>
            {importState.warnings.length > 0 && (
              <ul className="mt-1 list-disc ps-5 text-text-muted">
                {importState.warnings.map((warning, index) => (
                  <li key={index}>{noteText(warning)}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </>
  );
}
