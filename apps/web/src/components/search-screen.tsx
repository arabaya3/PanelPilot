'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import { SearchIcon } from '@/components/icons';
import { acquireTrial } from '@/lib/session';
import { passageSourceUrl, searchDocuments, type SearchOutcome } from '@/lib/search';

type RetrievedPassage = components['schemas']['RetrievedPassage'];

/**
 * The manufacturers the corpus is crawled from, as their chunks name them.
 * Mirrors the crawler allow-list; a name not here would match nothing.
 */
const MANUFACTURERS = [
  'ABB',
  'Siemens',
  'Schneider Electric',
  'Danfoss',
  'Yaskawa',
  'Rockwell Automation',
  'Mitsubishi Electric',
  'WEG',
  'Omron',
  'Delta Electronics',
  'LS Electric',
  'Inovance',
  'Hitachi',
  'Fuji Electric',
  'Nidec Control Techniques',
  'SEW-EURODRIVE',
  'Invertek Drives',
  'Lenze',
  'Phoenix Contact',
  'Weidmüller',
  'B&R',
] as const;

type Session =
  | { kind: 'starting' }
  | { kind: 'ready'; token: string }
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

type Results =
  | { kind: 'idle' }
  | { kind: 'searching' }
  | { kind: 'done'; query: string; passages: RetrievedPassage[] }
  | { kind: 'error'; reason: Exclude<SearchOutcome['kind'], 'loaded'> };

/**
 * `/search`: the verified manuals, searched directly.
 *
 * The chat answers a described fault; this is for when an engineer already
 * knows what they are looking for -- a parameter, a fault code, a wiring
 * table -- and wants the page, not a diagnosis. It searches production only:
 * every passage here is one a reviewer verified, and each links to the page
 * it came from.
 *
 * It runs on this browser's trial, resumed or started, like the chat. A
 * search is not charged as a question.
 */
export function SearchScreen({
  acquireImpl = acquireTrial,
  searchImpl = searchDocuments,
}: {
  acquireImpl?: typeof acquireTrial;
  searchImpl?: typeof searchDocuments;
}) {
  const t = useTranslations('search');
  const [session, setSession] = useState<Session>({ kind: 'starting' });
  const [query, setQuery] = useState('');
  const [manufacturer, setManufacturer] = useState('');
  const [results, setResults] = useState<Results>({ kind: 'idle' });
  const fieldId = useId();
  const brandId = useId();

  const connect = useCallback(async () => {
    setSession({ kind: 'starting' });
    const outcome = await acquireImpl();
    setSession(outcome.kind === 'ready' ? { kind: 'ready', token: outcome.accessToken } : outcome);
  }, [acquireImpl]);

  useEffect(() => {
    void connect();
  }, [connect]);

  async function run() {
    if (session.kind !== 'ready') return;
    const trimmed = query.trim();
    if (trimmed === '') return;
    setResults({ kind: 'searching' });
    const outcome = await searchImpl({
      token: session.token,
      query: trimmed,
      manufacturers: manufacturer ? [manufacturer] : [],
    });
    if (outcome.kind === 'loaded') {
      setResults({ kind: 'done', query: trimmed, passages: outcome.passages });
    } else {
      setResults({ kind: 'error', reason: outcome.kind });
      // A token that lapsed while the page sat open: get a fresh one, so the
      // next search works without a reload.
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  return (
    <AppShell>
      <div className="mb-6 flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight">{t('heading')}</h1>
        <p className="max-w-3xl text-text-muted">{t('intro')}</p>
      </div>

      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
        className="card mb-6 flex flex-col gap-3 p-4 md:flex-row md:items-end md:p-5"
      >
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <label htmlFor={fieldId} className="text-sm font-semibold">
            {t('label')}
          </label>
          <input
            id={fieldId}
            type="search"
            value={query}
            maxLength={4000}
            onChange={(event) => {
              setQuery(event.target.value);
            }}
            placeholder={t('placeholder')}
            className="input"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={brandId} className="text-sm font-semibold">
            {t('manufacturer')}
          </label>
          <select
            id={brandId}
            value={manufacturer}
            onChange={(event) => {
              setManufacturer(event.target.value);
            }}
            className="input"
          >
            <option value="">{t('anyManufacturer')}</option>
            {MANUFACTURERS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          disabled={session.kind !== 'ready' || query.trim() === '' || results.kind === 'searching'}
          className="btn btn-primary"
        >
          <SearchIcon width="16" height="16" />
          {results.kind === 'searching' ? t('searching') : t('submit')}
        </button>
      </form>

      {session.kind !== 'ready' && session.kind !== 'starting' && (
        <div
          role="alert"
          data-testid="search-session-failed"
          className="mb-6 flex flex-col items-start gap-3 rounded-lg border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
        >
          <p>{t(`session.${session.kind}`)}</p>
          <button type="button" onClick={() => void connect()} className="btn btn-sm btn-primary">
            {t('retry')}
          </button>
        </div>
      )}

      {results.kind === 'error' && (
        <p
          role="alert"
          data-testid="search-error"
          className="rounded-lg border border-severity-critical bg-severity-critical-surface p-4 text-sm text-severity-critical"
        >
          {t(`error.${results.reason}`)}
        </p>
      )}

      {results.kind === 'done' && (
        <section aria-labelledby={`${fieldId}-results`} className="flex flex-col gap-4">
          <h2 id={`${fieldId}-results`} className="text-sm font-semibold text-text-muted">
            {(t.raw('count') as string)
              .replace('{count}', String(results.passages.length))
              .replace('{query}', results.query)}
          </h2>
          {results.passages.length === 0 ? (
            <p data-testid="search-empty" className="card p-6 text-center text-sm text-text-muted">
              {t('empty')}
            </p>
          ) : (
            <ol className="flex flex-col gap-4" data-testid="search-results">
              {results.passages.map((passage) => (
                <li key={passage.id}>
                  <PassageCard passage={passage} />
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </AppShell>
  );
}

function PassageCard({ passage }: { passage: RetrievedPassage }) {
  const t = useTranslations('search');
  const source = passageSourceUrl(passage);
  const { manufacturer, section, page } = passage.citation;

  return (
    <article className="card p-4 md:p-5" data-testid={`passage-${passage.id}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        {manufacturer && <span className="chip">{manufacturer}</span>}
        {section && (
          <span dir="auto" className="font-semibold text-text">
            {section}
          </span>
        )}
        {typeof page === 'number' && (
          <span className="text-text-muted">
            {(t.raw('page') as string).replace('{page}', String(page))}
          </span>
        )}
      </div>
      {/* Manual text keeps its line breaks: a parameter table flattened into
          one paragraph is unreadable. `dir="auto"` because the manual has its
          own language: laid out in an Arabic or Hebrew page's direction,
          "23.12 Acceleration time 1" came out as "Acceleration time 1 23.12"
          -- the parameter number moved to the other end of the line. */}
      <p dir="auto" className="whitespace-pre-line break-words text-sm leading-relaxed text-text">
        {passage.text}
      </p>
      {source !== null && (
        <a
          href={source}
          target="_blank"
          rel="noreferrer"
          className="link mt-3 inline-block text-sm"
          data-testid={`passage-source-${passage.id}`}
        >
          {t('openSource')}
        </a>
      )}
    </article>
  );
}
