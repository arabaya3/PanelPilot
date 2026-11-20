'use client';

/**
 * M19: review unsaved work against its base and the latest saved revision.
 *
 * Read-only. Both revisions are read with the request that opens a revision
 * (tenant scope and snapshot integrity unchanged). Your work is a copy of the
 * editor's inputs captured when the review is opened or refreshed; nothing
 * here writes, and nothing reaches the editor, its drafts, its confirmations
 * or its publishing base. The base is never replaced by the latest revision.
 * Contents are shown as text only.
 */

import { useLocale } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import { unitOf, type Raw, type Scalar } from '@/components/design/revision-compare-model';
import {
  outsideChanges,
  storedSource,
  threeWay,
  type Review,
  type Row,
  type Source,
  type Status,
} from '@/components/design/three-way-model';
import { openProject, type SavedProject } from '@/lib/design';

/** Your work as captured from the editor. */
export type Captured = Source & { mark: string };

type Summary = SavedProject['revisions'][number];
type Outcome =
  | {
      key: string;
      kind: 'done';
      review: Review;
      outside: string[];
      base: Summary | null;
      baseRevision: number;
      latest: SavedProject;
      fetchedAt: Date;
      capturedAt: Date;
      mark: string;
    }
  | { key: string; kind: 'noBase'; madeFrom: number | null }
  | {
      key: string;
      kind: 'baseIntegrity' | 'latestIntegrity' | 'baseNotFound' | 'latestNotFound' | 'failed';
      revision?: number;
    };

const INTEGRITY = new Set(['snapshot_integrity_failed', 'snapshot_schema_unknown']);
const failedIntegrity = (p: SavedProject) =>
  (p.findings as unknown as Raw[]).some((f) => INTEGRITY.has(String(f.code)));

const ORDER: Status[] = ['conflict', 'unresolved', 'both', 'yours', 'latest', 'same'];

export function ThreeWayReview({
  token,
  projectId,
  base,
  madeFrom = null,
  capture,
  workMark,
  onClose,
  onUnauthorized,
  impls = {},
}: {
  token: string;
  projectId: string;
  /** The editing base; null when it cannot be shown (a held base). */
  base: number | null;
  /** For a held base: the revision the work says it was made from. */
  madeFrom?: number | null;
  /** A copy of the editor's inputs, now. */
  capture: () => Captured;
  /** What the editor holds now (to tell a captured copy is outdated). */
  workMark: string;
  onClose: () => void;
  onUnauthorized?: () => void;
  impls?: Partial<{ open: typeof openProject }>;
}) {
  const t = useIsolatingTranslations('design.review');
  const tc = useIsolatingTranslations('design.compare');
  const locale = useLocale();
  const id = useId();
  const { open = openProject } = impls;
  const [round, setRound] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const key = `${token}|${projectId}|${String(base)}|${String(round)}`;
  const keyNow = useRef(key);
  keyNow.current = key;

  async function run(asked: string) {
    if (base === null) {
      setOutcome({ key: asked, kind: 'noBase', madeFrom });
      return;
    }
    const captured = capture();
    const capturedAt = new Date();
    setPending(asked);
    let pair: Awaited<ReturnType<typeof open>>[];
    try {
      pair = await Promise.all([
        open({ token, id: projectId, revision: base }),
        open({ token, id: projectId, revision: null }),
      ]);
    } catch {
      pair = [{ kind: 'failed' }, { kind: 'failed' }];
    }
    setPending((current) => (current === asked ? null : current));
    // Input, project, base, identity or round changed since: this answer is not shown.
    if (keyNow.current !== asked) return;
    const [b = { kind: 'failed' as const }, l = { kind: 'failed' as const }] = pair;
    if (b.kind === 'unauthorized' || l.kind === 'unauthorized') {
      onUnauthorized?.();
      setOutcome({ key: asked, kind: 'failed' });
      return;
    }
    if (b.kind !== 'saved') {
      setOutcome({
        key: asked,
        kind: b.kind === 'refused' ? 'baseNotFound' : 'failed',
        revision: base,
      });
      return;
    }
    if (b.project.revision !== base) {
      setOutcome({ key: asked, kind: 'failed' });
      return;
    }
    if (failedIntegrity(b.project)) {
      setOutcome({ key: asked, kind: 'baseIntegrity', revision: base });
      return;
    }
    if (l.kind !== 'saved') {
      setOutcome({ key: asked, kind: l.kind === 'refused' ? 'latestNotFound' : 'failed' });
      return;
    }
    if (failedIntegrity(l.project)) {
      setOutcome({ key: asked, kind: 'latestIntegrity', revision: l.project.revision });
      return;
    }
    const baseSource = storedSource(b.project);
    const latestSource = storedSource(l.project);
    setOutcome({
      key: asked,
      kind: 'done',
      review: threeWay(baseSource, captured, latestSource),
      outside: outsideChanges(b.project, l.project, baseSource, latestSource),
      base: b.project.revisions.find((r) => r.number === base) ?? null,
      baseRevision: base,
      latest: l.project,
      fetchedAt: new Date(),
      capturedAt,
      mark: captured.mark,
    });
  }

  useEffect(() => {
    void run(key);
    // Each round (opening, Refresh) runs once; the key changes with it.
  }, [key]);

  // On opening, focus moves to the review's heading (it appears in place of nothing).
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const shown = outcome !== null && outcome.key === key ? outcome : null;
  const busy = pending === key;
  const when = (date: Date | string) => {
    const d = typeof date === 'string' ? new Date(date) : date;
    return Number.isNaN(d.getTime())
      ? String(date)
      : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(d);
  };
  const outdated = shown?.kind === 'done' && shown.mark !== workMark;
  const counts = (review: Review) => {
    const map = new Map<Status, number>();
    for (const row of review.sections.flatMap((s) => s.rows))
      map.set(row.status, (map.get(row.status) ?? 0) + 1);
    return map;
  };
  const status = busy
    ? t('loading')
    : shown === null
      ? ''
      : shown.kind === 'done'
        ? t('done', { count: shown.review.sections.reduce((n, s) => n + s.rows.length, 0) })
        : shown.kind === 'noBase'
          ? t('noBase')
          : t(shown.kind, { revision: shown.revision ?? '' });

  const label = (field: string) => {
    const k = `fields.${field.replace(/\./g, '_')}`;
    return tc.has(k) ? tc(k) : field;
  };
  const value = (v: Scalar | undefined, unit?: string) =>
    v === undefined || v === null
      ? tc('notSet')
      : typeof v === 'boolean'
        ? tc(v ? 'yes' : 'no')
        : unit
          ? `${String(v)} ${unit}`
          : String(v);

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="mt-3 rounded-md border border-border p-3"
      data-testid="three-way-review"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`${id}-title`} ref={headingRef} tabIndex={-1} className="panel-title">
          {t('title')}
        </h3>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            data-testid="review-refresh"
            onClick={() => {
              // Always allowed: a newer round makes any answer still on its way stale.
              setRound((n) => n + 1);
            }}
          >
            {t('refresh')}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            data-testid="review-close"
            onClick={onClose}
          >
            {t('close')}
          </button>
        </div>
      </div>
      <p className="mt-1 text-sm text-text-muted">{t('scope')}</p>
      <details className="mt-1 text-sm">
        <summary>{t('coverage')}</summary>
        <ul className="mt-1 list-disc ps-5">
          <li>{t('coverageCompared')}</li>
          <li>{t('coverageNot')}</li>
          <li>{t('coverageMatching')}</li>
        </ul>
      </details>
      {/* The one live region: what the review came to. */}
      <p role="status" className="mt-2 text-sm empty:hidden" data-testid="review-status">
        {status}
      </p>
      {outdated ? (
        <p
          className="mt-2 rounded-md border border-status-warning p-2 text-sm"
          data-testid="review-outdated"
        >
          {t('outdated')}
        </p>
      ) : null}
      {shown?.kind === 'noBase' ? (
        <p className="mt-2 text-sm" data-testid="review-no-base">
          {shown.madeFrom !== null
            ? t('noBaseHeld', { revision: shown.madeFrom })
            : t('noBaseExplain')}
        </p>
      ) : null}
      {shown && shown.kind !== 'done' && shown.kind !== 'noBase' ? (
        <p className="mt-2 text-sm" data-testid="review-unavailable">
          {t('cannotEstablish')}
        </p>
      ) : null}
      {shown?.kind === 'done' ? (
        <div className="mt-3" data-testid="review-result">
          <div className="grid gap-2 sm:grid-cols-3">
            <div
              className="rounded-md border border-border p-2 text-sm"
              data-testid="review-source-base"
            >
              <p className="font-semibold">{t('sourceBase', { revision: shown.baseRevision })}</p>
              {shown.base ? <p>{when(shown.base.created_at)}</p> : null}
              {shown.base ? (
                <p>
                  {shown.base.author_unknown || shown.base.author === ''
                    ? tc('authorNotRecorded')
                    : tc('author', { author: shown.base.author })}
                </p>
              ) : null}
            </div>
            <div
              className="rounded-md border border-border p-2 text-sm"
              data-testid="review-source-yours"
            >
              <p className="font-semibold">{t('sourceYours')}</p>
              <p>{t('capturedAt', { time: when(shown.capturedAt) })}</p>
            </div>
            <div
              className="rounded-md border border-border p-2 text-sm"
              data-testid="review-source-latest"
            >
              <p className="font-semibold">
                {t('sourceLatest', { revision: shown.latest.revision })}
              </p>
              <p>{t('fetchedAt', { time: when(shown.fetchedAt) })}</p>
              <p className="text-text-muted">{t('latestCaveat')}</p>
            </div>
          </div>
          {shown.latest.revision === shown.baseRevision ? (
            <p className="mt-2 text-sm" data-testid="review-no-newer">
              {t('noNewer', { revision: shown.baseRevision })}
            </p>
          ) : null}
          <ul className="mt-3 text-sm" data-testid="review-summary">
            {ORDER.filter((s) => (counts(shown.review).get(s) ?? 0) > 0).map((s) => (
              <li key={s}>
                {t(`statuses.${s}`)}: {counts(shown.review).get(s)}
              </li>
            ))}
          </ul>
          {shown.outside.length > 0 ? (
            <div
              className="mt-3 rounded-md border border-border p-2 text-sm"
              data-testid="review-outside"
            >
              <p className="font-semibold">{t('outside')}</p>
              <ul className="list-disc ps-5">
                {shown.outside.map((path) => (
                  <li key={path} className="break-words">
                    <bdi>{path}</bdi>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {shown.review.sections.map((section) => (
            <details
              key={section.id}
              open={section.status === 'compared' && section.rows.length > 0}
              className="mt-3 rounded-md border border-border-subtle p-2"
              data-testid={`review-section-${section.id}`}
            >
              <summary className="font-semibold">
                {tc(`sections.${section.id}`)}
                {' — '}
                {section.status === 'unavailable'
                  ? t('unavailableIn', {
                      sources: section.unavailableIn.map((s) => t(`sources.${s}`)).join(', '),
                    })
                  : section.rows.length === 0
                    ? t('noChanges')
                    : tc('changes', { count: section.rows.length })}
              </summary>
              {section.status === 'unavailable' ? (
                <p className="mt-1 text-sm">{t('unavailableExplain')}</p>
              ) : (
                section.rows.map((row) => (
                  <RowView key={row.key} row={row} t={t} tc={tc} label={label} value={value} />
                ))
              )}
            </details>
          ))}
        </div>
      ) : null}
    </section>
  );
}

type T = ReturnType<typeof useIsolatingTranslations>;

function RowView({
  row,
  t,
  tc,
  label,
  value,
}: {
  row: Row;
  t: T;
  tc: T;
  label: (field: string) => string;
  value: (v: Scalar | undefined, unit?: string) => string;
}) {
  const presence = (side: 'base' | 'yours' | 'latest') =>
    row.presence[side] ? '' : ` (${t('absent')})`;
  const name =
    row.label === 'project'
      ? tc('labels.project')
      : row.type === 'wires'
        ? t('wiresOf', { role: row.label })
        : row.label;
  return (
    <div className="mt-3 text-sm" data-testid="review-row" data-status={row.status}>
      <p>
        <span className="font-semibold">{t(`statuses.${row.status}`)}</span>{' '}
        {row.type === 'details' || row.type === 'wires' ? '' : tc(`types.${row.type}`)}{' '}
        <bdi className="break-words">{name}</bdi>
        {row.where ? (
          <span className="text-text-muted"> ({tc('where', { where: row.where })})</span>
        ) : null}
      </p>
      {row.presence.base !== row.presence.yours || row.presence.base !== row.presence.latest ? (
        <p className="text-text-muted">
          {t('presence', {
            base: row.presence.base ? t('present') : t('absent'),
            yours: row.presence.yours ? t('present') : t('absent'),
            latest: row.presence.latest ? t('present') : t('absent'),
          })}
        </p>
      ) : null}
      {row.fields.length > 0 ? (
        <div className="max-w-full overflow-x-auto">
          <table className="data-table mt-1 w-full text-sm">
            <thead>
              <tr>
                <th className="p-1 text-start">{tc('field')}</th>
                <th className="p-1 text-start">
                  {t('sources.base')}
                  {presence('base')}
                </th>
                <th className="p-1 text-start">
                  {t('sources.yours')}
                  {presence('yours')}
                </th>
                <th className="p-1 text-start">
                  {t('sources.latest')}
                  {presence('latest')}
                </th>
                <th className="p-1 text-start">{t('fieldStatus')}</th>
              </tr>
            </thead>
            <tbody>
              {row.fields.map((f) => (
                <tr key={f.field}>
                  <td className="break-words p-1">{label(f.field)}</td>
                  <td className="break-words p-1">
                    <bdi>{value(f.base, f.unit ?? unitOf(f.field))}</bdi>
                  </td>
                  <td className="break-words p-1">
                    <bdi>{value(f.yours, f.unit ?? unitOf(f.field))}</bdi>
                  </td>
                  <td className="break-words p-1">
                    <bdi>{value(f.latest, f.unit ?? unitOf(f.field))}</bdi>
                  </td>
                  <td className="p-1">{t(`statuses.${f.status}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {row.sides ? (
        <div className="mt-1 rounded-md border border-border p-2">
          <p className="font-semibold">
            {t(row.reason === 'wire_endpoints' ? 'unresolvedWires' : 'unresolvedDuplicate')}
          </p>
          <div className="mt-1 grid gap-2 sm:grid-cols-3">
            {(['base', 'yours', 'latest'] as const).map((side) => (
              <div key={side}>
                <p className="font-semibold">{t(`sources.${side}`)}</p>
                {(row.sides?.[side] ?? []).length === 0 ? (
                  <p className="text-text-muted">{tc('none')}</p>
                ) : (
                  <ol className="list-decimal ps-5">
                    {(row.sides?.[side] ?? []).map((entry, index) => (
                      <li key={index} className="break-words">
                        {Object.entries(entry)
                          .map(([field, v]) => `${label(field)}: ${value(v, unitOf(field))}`)
                          .join('; ')}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {row.dependsOn.length > 0 ? (
        <ul className="mt-1 list-disc ps-5 text-text-muted" data-testid="review-depends">
          {row.dependsOn.map((dep, index) => (
            <li key={index}>
              {t(dep.side === 'latest' ? 'dependsLatest' : 'dependsYours', {
                what: `${tc(`types.${dep.type}`)} ${dep.label}`,
              })}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
