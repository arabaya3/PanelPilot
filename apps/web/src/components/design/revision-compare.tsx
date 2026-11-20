'use client';

/**
 * M18: compare two saved revisions of the project open, read-only.
 *
 * Both revisions are read with the same request that opens a revision, so the
 * server's tenant scope and snapshot integrity checks apply unchanged. Nothing
 * here writes, and nothing reaches the editor: the work on the page, its
 * drafts and its editing base stay exactly as they are.
 *
 * Contents come from the server and are shown as text only.
 */

import { useLocale } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import {
  changedCount,
  compareRevisions,
  type Ambiguous,
  type Comparison,
  type FieldChange,
  type Item,
  type Scalar,
  type Section,
  unitOf,
} from '@/components/design/revision-compare-model';
import { openProject, type SavedProject } from '@/lib/design';

type Summary = SavedProject['revisions'][number];

type Outcome =
  | { key: string; kind: 'compared'; comparison: Comparison; before: Summary; after: Summary }
  | { key: string; kind: 'notFound' }
  | { key: string; kind: 'failed' };

const FIELD_KEYS = new Set([
  'name',
  'number',
  'customer',
  'consultant',
  'contractor',
  'location',
  'fed_from',
  'feeder_length_m',
  'supply.voltage_v',
  'supply.phases',
  'supply.frequency_hz',
  'supply.earthing',
  'supply.fault_level_ka',
  'supply.earth_loop_ohm',
  'conditions.installation_method',
  'conditions.ambient_temp_c',
  'conditions.grouped_circuits',
  'conditions.conductor_material',
  'conditions.insulation_rating_c',
  'load',
  'power_kw',
  'phases',
  'power_factor',
  'controlled',
  'feeds',
  'starter',
  'length_m',
  'cross_section_mm2',
  'colour',
  'validated',
  'rules_version',
  'snapshot_schema',
  'count',
  'pattern',
  'circuit',
  'template',
  'board',
  'colour.value',
  'colour.source',
  'number.value',
  'number.source',
  'cross_section_mm2.value',
  'cross_section_mm2.source',
  'signal',
  'scope',
  'members',
  'connections',
  'device_kind',
  'strip',
  'offset',
  'level',
  'kind',
  'severity',
]);

export function RevisionCompare({
  token,
  projectId,
  revisions,
  onUnauthorized,
  impls = {},
}: {
  token: string;
  projectId: string;
  /** The project's revisions as the server listed them, oldest first. */
  revisions: Summary[];
  onUnauthorized?: () => void;
  impls?: Partial<{ open: typeof openProject }>;
}) {
  const t = useIsolatingTranslations('design.compare');
  const locale = useLocale();
  const id = useId();
  const { open = openProject } = impls;
  const numbers = revisions.map((revision) => revision.number);
  const latest = numbers[numbers.length - 1] ?? 1;
  const previous = numbers[numbers.length - 2] ?? latest;
  const [before, setBefore] = useState(previous);
  const [after, setAfter] = useState(latest);
  // The comparison waiting for an answer (busy only while it is the one chosen now).
  const [pending, setPending] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  // What the answer must still be about to be shown: this identity, project and both choices.
  const key = `${token}|${projectId}|${String(before)}|${String(after)}`;
  const keyNow = useRef(key);
  keyNow.current = key;
  const busy = pending === key;
  // Another project or identity: nothing compared for the last one stays.
  const scope = `${token}|${projectId}`;
  useEffect(() => {
    setOutcome(null);
    setPending(null);
    setBefore(previous);
    setAfter(latest);
    // Only when the project or identity changes.
  }, [scope]);

  async function compare() {
    if (before === after || busy) return;
    const asked = key;
    setPending(asked);
    setOutcome(null);
    const [a, b] = await Promise.all([
      open({ token, id: projectId, revision: before }),
      open({ token, id: projectId, revision: after }),
    ]);
    // A choice, project or identity changed meanwhile: this answer is not shown.
    if (keyNow.current !== asked) return;
    setPending(null);
    if (a.kind === 'unauthorized' || b.kind === 'unauthorized') {
      onUnauthorized?.();
      setOutcome({ key: asked, kind: 'failed' });
      return;
    }
    if (a.kind !== 'saved' || b.kind !== 'saved') {
      const notFound = [a, b].some((x) => x.kind === 'refused');
      setOutcome({ key: asked, kind: notFound ? 'notFound' : 'failed' });
      return;
    }
    const summaryOf = (number: number, project: SavedProject) =>
      project.revisions.find((revision) => revision.number === number) ??
      revisions.find((revision) => revision.number === number);
    const sa = summaryOf(before, a.project);
    const sb = summaryOf(after, b.project);
    // The server answered for other revisions than asked: never shown as these.
    if (a.project.revision !== before || b.project.revision !== after || !sa || !sb) {
      setOutcome({ key: asked, kind: 'failed' });
      return;
    }
    setOutcome({
      key: asked,
      kind: 'compared',
      comparison: compareRevisions(a.project, b.project),
      before: sa,
      after: sb,
    });
  }

  const shown = outcome !== null && outcome.key === key ? outcome : null;
  const when = (iso: string) => {
    const date = new Date(iso);
    return Number.isNaN(date.getTime())
      ? iso
      : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  };
  const status = busy
    ? t('comparing')
    : shown?.kind === 'notFound'
      ? t('notFound')
      : shown?.kind === 'failed'
        ? t('failed')
        : shown?.kind === 'compared' && shown.comparison.kind === 'integrity'
          ? t('integrity', { revisions: shown.comparison.failed.join(', ') })
          : shown?.kind === 'compared' && shown.comparison.kind === 'compared'
            ? t('done', {
                count: shown.comparison.sections.reduce(
                  (n: number, s: Section) => n + changedCount(s),
                  0,
                ),
              })
            : before === after
              ? t('sameChosen')
              : '';

  const fieldLabel = (field: string) =>
    FIELD_KEYS.has(field) ? t(`fields.${field.replace(/\./g, '_')}`) : field;
  const value = (v: Scalar | undefined, unit?: string) =>
    v === undefined || v === null
      ? t('notSet')
      : typeof v === 'boolean'
        ? t(v ? 'yes' : 'no')
        : unit
          ? `${String(v)} ${unit}`
          : String(v);

  return (
    <section aria-labelledby={`${id}-title`} className="mt-4" data-testid="revision-compare">
      <h3 id={`${id}-title`} className="panel-title">
        {t('title')}
      </h3>
      <p className="mt-1 text-sm text-text-muted">{t('scope')}</p>
      <details className="mt-1 text-sm">
        <summary>{t('scopeDetails')}</summary>
        <ul className="mt-1 list-disc ps-5">
          <li>{t('scopeCompared')}</li>
          <li>{t('scopeOutputs')}</li>
          <li>{t('scopeNotCompared')}</li>
          <li>{t('scopeMatching')}</li>
        </ul>
      </details>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-sm">
          <span>{t('before')}</span>
          <select
            className="input"
            value={before}
            data-testid="compare-before"
            onChange={(event) => {
              setBefore(Number(event.target.value));
            }}
          >
            {numbers.map((n) => (
              <option key={n} value={n}>
                {t('revisionOption', { revision: n })}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          <span>{t('after')}</span>
          <select
            className="input"
            value={after}
            data-testid="compare-after"
            onChange={(event) => {
              setAfter(Number(event.target.value));
            }}
          >
            {numbers.map((n) => (
              <option key={n} value={n}>
                {t('revisionOption', { revision: n })}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          // Busy is only announced, never a native disabled: focus stays on the button
          // while it works (a disabled button drops focus to the page).
          disabled={before === after}
          aria-disabled={busy}
          data-testid="compare-run"
          onClick={() => {
            void compare();
          }}
        >
          {t('run')}
        </button>
      </div>
      {/* The one live region: what the comparison came to. */}
      <p role="status" className="mt-2 text-sm empty:hidden" data-testid="compare-status">
        {status}
      </p>
      {shown?.kind === 'compared' ? (
        <div className="mt-3" data-testid="compare-result">
          <div className="grid gap-2 sm:grid-cols-2">
            {(['before', 'after'] as const).map((side) => {
              const summary = shown[side];
              return (
                <div
                  key={side}
                  className="rounded-md border border-border p-2 text-sm"
                  data-testid={`compare-side-${side}`}
                >
                  <p className="font-semibold">
                    {t(side === 'before' ? 'beforeRevision' : 'afterRevision', {
                      revision: summary.number,
                    })}
                  </p>
                  <p>{when(summary.created_at)}</p>
                  <p>
                    {summary.author_unknown || summary.author === ''
                      ? t('authorNotRecorded')
                      : t('author', { author: summary.author })}
                  </p>
                  {summary.note ? (
                    <p className="break-words text-text-muted">{summary.note}</p>
                  ) : null}
                </div>
              );
            })}
          </div>
          {shown.comparison.kind === 'integrity' ? (
            <p role="alert" className="mt-3 text-sm" data-testid="compare-integrity">
              {t('integrityDetail', { revisions: shown.comparison.failed.join(', ') })}
            </p>
          ) : (
            <Sections
              sections={shown.comparison.sections}
              baseId={id}
              t={t}
              fieldLabel={fieldLabel}
              value={value}
            />
          )}
        </div>
      ) : null}
    </section>
  );
}

type T = ReturnType<typeof useIsolatingTranslations>;

function Sections({
  sections,
  baseId,
  t,
  fieldLabel,
  value,
}: {
  sections: Section[];
  baseId: string;
  t: T;
  fieldLabel: (field: string) => string;
  value: (v: Scalar | undefined, unit?: string) => string;
}) {
  const anchor = (s: Section) => `${baseId.replace(/:/g, '')}-${s.id}`;
  return (
    <>
      <nav aria-label={t('summary')} className="mt-3">
        <ul className="text-sm" data-testid="compare-summary">
          {sections.map((s) => (
            <li key={s.id}>
              <a className="link" href={`#${anchor(s)}`}>
                {t(`sections.${s.id}`)}
              </a>
              {': '}
              {s.status === 'unavailable'
                ? t('unavailableIn', { revisions: s.unavailableIn.join(', ') })
                : changedCount(s) === 0
                  ? t('noChanges')
                  : t('changes', { count: changedCount(s) })}
            </li>
          ))}
        </ul>
      </nav>
      {sections.map((s) => {
        const n = changedCount(s);
        const changed = s.items.filter((item) => item.kind !== 'unchanged');
        return (
          <details
            key={s.id}
            id={anchor(s)}
            open={s.status === 'compared' && n > 0}
            className="mt-3 rounded-md border border-border-subtle p-2"
            data-testid={`compare-section-${s.id}`}
          >
            <summary className="font-semibold">
              {t(`sections.${s.id}`)}
              {' — '}
              {s.status === 'unavailable'
                ? t('unavailableIn', { revisions: s.unavailableIn.join(', ') })
                : n === 0
                  ? t('noChanges')
                  : t('changes', { count: n })}
            </summary>
            {s.status === 'unavailable' ? (
              <p className="mt-1 text-sm">{t('unavailableExplain')}</p>
            ) : (
              <>
                {changed.map((item, index) => (
                  <ItemView
                    key={`${item.type}-${item.label}-${String(index)}`}
                    item={item}
                    t={t}
                    fieldLabel={fieldLabel}
                    value={value}
                  />
                ))}
                {s.ambiguous.map((group, index) => (
                  <AmbiguousView
                    key={`a-${group.label}-${String(index)}`}
                    group={group}
                    t={t}
                    fieldLabel={fieldLabel}
                    value={value}
                  />
                ))}
                {s.items.length - changed.length > 0 ? (
                  <p className="mt-1 text-sm text-text-muted">
                    {t('unchangedCount', { count: s.items.length - changed.length })}
                  </p>
                ) : null}
              </>
            )}
          </details>
        );
      })}
    </>
  );
}

function ItemView({
  item,
  t,
  fieldLabel,
  value,
}: {
  item: Item;
  t: T;
  fieldLabel: (field: string) => string;
  value: (v: Scalar | undefined, unit?: string) => string;
}) {
  const children = (item.children ?? []).filter((child) => child.kind !== 'unchanged');
  return (
    <div className="mt-2 text-sm" data-testid="compare-item" data-kind={item.kind}>
      <p>
        <span className="font-semibold">{t(`kinds.${item.kind}`, { count: item.count ?? 1 })}</span>{' '}
        {t(`types.${item.type}`)}{' '}
        <bdi className="break-words">
          {item.label === 'project' || item.label === 'design' || item.label === 'stored_design'
            ? t(`labels.${item.label}`)
            : item.label}
        </bdi>
        {item.where ? (
          <span className="text-text-muted"> ({t('where', { where: item.where })})</span>
        ) : null}
      </p>
      {item.changes.length > 0 ? (
        <Changes changes={item.changes} t={t} fieldLabel={fieldLabel} value={value} />
      ) : null}
      {item.values && Object.keys(item.values).length > 0 ? (
        <ul className="ms-4 list-disc ps-4 text-text-muted">
          {Object.entries(item.values).map(([field, v]) => (
            <li key={field} className="break-words">
              {fieldLabel(field)}: <bdi>{value(v, unitOf(field))}</bdi>
            </li>
          ))}
        </ul>
      ) : null}
      {children.length > 0 ? (
        <div className="ms-4 border-s border-border-subtle ps-3">
          {children.map((child, index) => (
            <ItemView
              key={`${child.type}-${child.label}-${String(index)}`}
              item={child}
              t={t}
              fieldLabel={fieldLabel}
              value={value}
            />
          ))}
        </div>
      ) : null}
      {(item.ambiguous ?? []).map((group, index) => (
        <div key={`g-${group.label}-${String(index)}`} className="ms-4">
          <AmbiguousView group={group} t={t} fieldLabel={fieldLabel} value={value} />
        </div>
      ))}
    </div>
  );
}

function Changes({
  changes,
  t,
  fieldLabel,
  value,
}: {
  changes: FieldChange[];
  t: T;
  fieldLabel: (field: string) => string;
  value: (v: Scalar | undefined, unit?: string) => string;
}) {
  return (
    <div className="max-w-full overflow-x-auto">
      <table className="data-table mt-1 w-full text-sm">
        <thead>
          <tr>
            <th className="p-1 text-start">{t('field')}</th>
            <th className="p-1 text-start">{t('beforeShort')}</th>
            <th className="p-1 text-start">{t('afterShort')}</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr key={change.field}>
              <td className="break-words p-1">{fieldLabel(change.field)}</td>
              <td className="break-words p-1">
                <bdi>{value(change.before, change.unit)}</bdi>
              </td>
              <td className="break-words p-1">
                <bdi>{value(change.after, change.unit)}</bdi>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AmbiguousView({
  group,
  t,
  fieldLabel,
  value,
}: {
  group: Ambiguous;
  t: T;
  fieldLabel: (field: string) => string;
  value: (v: Scalar | undefined, unit?: string) => string;
}) {
  const side = (entries: Record<string, Scalar>[]) =>
    entries.length === 0 ? (
      <p className="text-text-muted">{t('none')}</p>
    ) : (
      <ol className="list-decimal ps-5">
        {entries.map((entry, index) => (
          <li key={index} className="break-words">
            {Object.entries(entry)
              .map(([field, v]) => `${fieldLabel(field)}: ${value(v, unitOf(field))}`)
              .join('; ')}
          </li>
        ))}
      </ol>
    );
  return (
    <div
      className="mt-2 rounded-md border border-border p-2 text-sm"
      data-testid="compare-ambiguous"
    >
      <p className="font-semibold">
        {t(group.reason === 'duplicate' ? 'ambiguousDuplicate' : 'ambiguousWires', {
          label: group.label,
          before: group.before.length,
          after: group.after.length,
        })}
      </p>
      <div className="mt-1 grid gap-2 sm:grid-cols-2">
        <div>
          <p className="font-semibold">{t('beforeShort')}</p>
          {side(group.before)}
        </div>
        <div>
          <p className="font-semibold">{t('afterShort')}</p>
          {side(group.after)}
        </div>
      </div>
    </div>
  );
}
