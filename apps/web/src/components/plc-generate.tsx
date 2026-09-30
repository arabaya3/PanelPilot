'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { BoltIcon, CodeIcon } from '@/components/icons';
import { PlcView } from '@/components/plc-view';
import { generatePlc, type GenerateOutcome, type PlcDialect, type PlcLanguage } from '@/lib/plc';
import { acquireTrial } from '@/lib/session';

type PlcGenerationResult = components['schemas']['PlcGenerationResult'];

const DIALECTS: readonly PlcDialect[] = ['iec-61131-3', 'siemens-scl', 'rockwell-st', 'codesys-st'];

type Generation =
  | { kind: 'idle' }
  | { kind: 'generating' }
  | { kind: 'generated'; result: PlcGenerationResult }
  | { kind: 'error'; reason: Exclude<GenerateOutcome['kind'], 'generated'> | 'session' }
  | { kind: 'rejected'; detail: string | null };

/**
 * Describe what the program should do; get code with its check attached.
 *
 * The model writes, the parser judges: the verdict banner above the code is
 * the same one a pasted program gets, and code the check could not pass says
 * so. The session is started only when the first program is asked for -- the
 * check next door needs none.
 */
export function PlcGenerate({
  generateImpl = generatePlc,
  acquireImpl = acquireTrial,
}: {
  generateImpl?: typeof generatePlc;
  acquireImpl?: typeof acquireTrial;
}) {
  const t = useTranslations('plc');
  const [description, setDescription] = useState('');
  const [dialect, setDialect] = useState<PlcDialect>('iec-61131-3');
  const [language, setLanguage] = useState<PlcLanguage>('structured-text');
  const [token, setToken] = useState<string | null>(null);
  const [generation, setGeneration] = useState<Generation>({ kind: 'idle' });
  const descriptionId = useId();
  const dialectId = useId();
  const languageId = useId();

  async function sessionToken(): Promise<string | null> {
    if (token !== null) return token;
    const acquired = await acquireImpl();
    if (acquired.kind !== 'ready') return null;
    setToken(acquired.accessToken);
    return acquired.accessToken;
  }

  async function generate() {
    const trimmed = description.trim();
    if (trimmed === '') return;
    setGeneration({ kind: 'generating' });
    const current = await sessionToken();
    if (current === null) {
      setGeneration({ kind: 'error', reason: 'session' });
      return;
    }
    const outcome = await generateImpl({ token: current, description: trimmed, dialect, language });
    if (outcome.kind === 'generated') setGeneration({ kind: 'generated', result: outcome.result });
    else if (outcome.kind === 'rejected') setGeneration(outcome);
    else {
      // A lapsed token is dropped, so the next attempt fetches a fresh one.
      if (outcome.kind === 'unauthorized') setToken(null);
      setGeneration({ kind: 'error', reason: outcome.kind });
    }
  }

  return (
    <div className="grid items-start gap-5 lg:grid-cols-2">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void generate();
        }}
        className="card flex flex-col gap-3 p-4 md:p-5"
      >
        <label htmlFor={descriptionId} className="text-sm font-semibold">
          {t('describeLabel')}
        </label>
        <textarea
          id={descriptionId}
          value={description}
          onChange={(event) => {
            setDescription(event.target.value);
          }}
          rows={8}
          maxLength={4000}
          dir="auto"
          placeholder={t('describePlaceholder')}
          className="input font-sans text-sm"
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <label htmlFor={dialectId} className="text-sm font-semibold">
              {t('dialect')}
            </label>
            <select
              id={dialectId}
              value={dialect}
              onChange={(event) => {
                setDialect(event.target.value as PlcDialect);
              }}
              className="input"
            >
              {DIALECTS.map((value) => (
                <option key={value} value={value}>
                  {t(`dialects.${value}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={languageId} className="text-sm font-semibold">
              {t('language')}
            </label>
            <select
              id={languageId}
              value={language}
              onChange={(event) => {
                setLanguage(event.target.value as PlcLanguage);
              }}
              className="input"
            >
              <option value="structured-text">{t('languages.structured-text')}</option>
              <option value="ladder">{t('languages.ladder')}</option>
            </select>
          </div>
        </div>
        <div>
          <button
            type="submit"
            disabled={description.trim() === '' || generation.kind === 'generating'}
            className="btn btn-primary"
          >
            <BoltIcon width="16" height="16" />
            {generation.kind === 'generating' ? t('generating') : t('generate')}
          </button>
        </div>
      </form>

      <div className="flex min-w-0 flex-col gap-3">
        {generation.kind === 'generated' && (
          <>
            <p data-testid="plc-generated-note" className="text-sm text-text-muted">
              {t('generatedNote')}
            </p>
            <PlcView
              language={generation.result.language}
              source={generation.result.source ?? null}
              rungs={generation.result.rungs ?? []}
              validation={generation.result.validation}
            />
          </>
        )}
        {generation.kind === 'rejected' && (
          <p
            role="alert"
            data-testid="plc-generate-rejected"
            className="rounded-lg border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
          >
            {generation.detail ?? t('generateRejected')}
          </p>
        )}
        {generation.kind === 'error' && (
          <p
            role="alert"
            data-testid="plc-generate-error"
            className="rounded-lg border border-severity-critical bg-severity-critical-surface p-4 text-sm text-severity-critical"
          >
            {t(`generateError.${generation.reason}`)}
          </p>
        )}
        {(generation.kind === 'idle' || generation.kind === 'generating') && (
          <div className="flex min-h-96 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-border-subtle p-6 text-center text-sm text-text-muted">
            {generation.kind === 'generating' ? (
              <span
                aria-hidden="true"
                className="h-6 w-6 animate-spin rounded-full border-2 border-border-subtle border-t-accent"
              />
            ) : (
              <CodeIcon width="28" height="28" className="text-accent" />
            )}
            <p className="max-w-sm">{t('emptyGenerated')}</p>
          </div>
        )}
      </div>
    </div>
  );
}
