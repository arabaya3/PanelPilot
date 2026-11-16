'use client';

/**
 * M9c: the wire details of one binding, entered without JSON
 * (docs/architecture/m9c-guided-wire-details.md). Each wire connection of the
 * binding's pattern is named by its board, binding, pattern, net and readable
 * ends; the key the composer uses (`role/index`) is never asked for. A value
 * typed here is stored as entered by the user, which says who gave it, not that
 * it was checked. A value stored with another source is shown exactly and only
 * replaced or removed when the user explicitly asks, after being told what goes.
 */
import { useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import type { ConfigBinding, Configuration } from '@/components/design/configuration-model';
import {
  removeWireEntry,
  removeWireValue,
  setUserValue,
  WIRE_FIELDS,
  wireProblems,
  wireRows,
  type WireField,
  type WireRow,
  type WireValue,
} from '@/components/design/wire-model';

function focusLater(id: string) {
  window.setTimeout(() => {
    document.getElementById(id)?.focus();
  }, 0);
}

const BUTTON = 'btn btn-sm max-w-full whitespace-normal text-start';

type Pending =
  | { kind: 'field'; key: string; field: string }
  | { kind: 'replace'; key: string; field: WireField }
  | { kind: 'entry'; key: string };

export function WireDetails({
  value,
  onChange,
  binding,
  base,
  announce,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  binding: ConfigBinding;
  base: string;
  announce: (message: string) => void;
}) {
  const t = useIsolatingTranslations('configuration');
  // A confirmation holds the stored entry it was opened for; once that changes it is dropped.
  const [opened, setOpened] = useState<{ pending: Pending; snapshot: string } | null>(null);
  const storedWires = (binding.wires ?? {}) as Record<string, unknown>;
  const snapshotOf = (key: string) => JSON.stringify(storedWires[key] ?? null);
  const pending =
    opened !== null && opened.snapshot === snapshotOf(opened.pending.key) ? opened.pending : null;
  const setPending = (next: Pending | null) => {
    setOpened(next === null ? null : { pending: next, snapshot: snapshotOf(next.key) });
  };
  const rows = wireRows(value, binding);
  const problems = wireProblems(value, binding);
  const fieldId = (index: number, field: string) =>
    `${base}-wire-${String(index)}-${field.replace(/[^A-Za-z0-9_-]/g, '_')}`;
  const rowId = (index: number) => `${base}-wire-${String(index)}`;
  const fieldName = (field: string) =>
    (WIRE_FIELDS as readonly string[]).includes(field) ? t(`wires.field.${field}`) : field;
  const rowName = (row: WireRow) =>
    t('wires.rowName', {
      board: binding.board,
      binding: binding.id,
      pattern: binding.pattern,
      net: row.role,
      signal: row.signal || '—',
      from: row.from,
      to: row.to,
    });
  const shown = (v: WireValue) => (v.kind === 'given' ? v.value : '');
  const sourceText = (v: WireValue) => {
    if (v.kind === 'unset') return t('wires.source.unset');
    if (v.kind === 'unreadable') return t('wires.source.unreadable');
    if (v.source === 'user') return t('wires.source.user');
    if (v.source === 'data') return t('wires.source.data');
    if (v.source === 'assumption') {
      return t('wires.source.assumption', { statement: v.statement ?? '—' });
    }
    return t('wires.source.other', { source: v.source });
  };

  function confirmBox(text: string, action: string, onConfirm: () => void, testId: string) {
    return (
      <div
        className="flex min-w-0 flex-col gap-1 rounded-md border border-border-subtle p-2"
        role="group"
        aria-label={text}
        data-testid={testId}
      >
        <p className="text-xs" dir="auto">
          {text}
        </p>
        <div className="flex min-w-0 flex-wrap gap-2">
          <button
            type="button"
            className={`${BUTTON} btn-danger`}
            onClick={onConfirm}
            data-testid={`${testId}-confirm`}
          >
            {action}
          </button>
          <button
            type="button"
            className={`${BUTTON} btn-secondary`}
            onClick={() => {
              setPending(null);
            }}
            data-testid={`${testId}-cancel`}
          >
            {t('wires.cancel')}
          </button>
        </div>
      </div>
    );
  }

  const problemList: { id: string; text: string }[] = [];
  rows.forEach((row, index) => {
    for (const field of WIRE_FIELDS) {
      const problem = problems.values[`${row.key}|${field}`];
      if (problem !== undefined) {
        problemList.push({
          id: fieldId(index, field),
          text: t('wires.overviewItem', {
            wire: `${row.from} – ${row.to}`,
            field: fieldName(field),
            problem: t(`wires.problem.${problem}`),
          }),
        });
      }
    }
  });

  rows.forEach((row, index) => {
    if (row.unreadableEntry !== null) {
      problemList.push({
        id: `${rowId(index)}-unreadable`,
        text: t('wires.overviewEntry', {
          wire: `${row.from} – ${row.to}`,
          problem: t('wires.problem.entry_unreadable'),
        }),
      });
    }
    row.otherKeys.forEach((other) => {
      problemList.push({
        id: `${rowId(index)}-extra-${other.replace(/[^A-Za-z0-9_-]/g, '_')}`,
        text: t('wires.overviewEntry', {
          wire: `${row.from} – ${row.to}`,
          problem: t('wires.problem.extra_field', { name: other }),
        }),
      });
    });
  });
  problems.stray.forEach((key) => {
    problemList.push({
      id: `${base}-stray-${key.replace(/[^A-Za-z0-9_-]/g, '_')}`,
      text: t('wires.problem.stray', { key }),
    });
  });

  return (
    <section
      className="flex min-w-0 flex-col gap-2"
      aria-labelledby={`${base}-wires-title`}
      data-testid="wire-details"
    >
      <h4 id={`${base}-wires-title`} tabIndex={-1} className="text-sm font-semibold">
        {t('wires.title')}
      </h4>
      <p className="text-xs text-text-muted" data-testid="wire-details-note">
        {t('wires.note')}
      </p>
      {rows.length === 0 && (
        <p className="text-xs text-text-muted" data-testid="wire-details-none">
          {t('wires.none')}
        </p>
      )}
      {problemList.length > 0 && (
        <div className="flex min-w-0 flex-col gap-1" data-testid="wire-problems-overview">
          <p className="text-xs font-semibold">
            {t('wires.overview', { count: String(problemList.length) })}
          </p>
          <ul className="flex min-w-0 flex-col gap-1">
            {problemList.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="max-w-full whitespace-normal text-start text-xs underline"
                  onClick={() => {
                    document.getElementById(item.id)?.focus();
                  }}
                  data-testid="wire-problem-link"
                >
                  {item.text}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <ul className="flex min-w-0 flex-col gap-2">
        {rows.map((row, index) => {
          const name = rowName(row);
          const anyGiven =
            row.unreadableEntry !== null ||
            WIRE_FIELDS.some((f) => row.values[f].kind !== 'unset') ||
            row.otherKeys.length > 0;
          return (
            <li
              key={row.key}
              id={rowId(index)}
              tabIndex={-1}
              className="flex min-w-0 flex-col gap-2 rounded-md border border-border-subtle p-2"
              aria-label={name}
              data-testid="wire-row"
            >
              <p
                className="break-words text-xs font-semibold"
                dir="auto"
                data-testid="wire-row-name"
              >
                {name}
              </p>
              {row.unreadableEntry !== null && (
                <p className="text-xs font-semibold" dir="auto" data-testid="wire-unreadable">
                  {t('wires.problem.entry_unreadable')}
                </p>
              )}
              <div className="flex min-w-0 flex-wrap items-start gap-2">
                {WIRE_FIELDS.map((field) => {
                  const v = row.values[field];
                  const id = fieldId(index, field);
                  const problem = problems.values[`${row.key}|${field}`];
                  const editable =
                    row.unreadableEntry === null &&
                    (v.kind === 'unset' || (v.kind === 'given' && v.source === 'user'));
                  const described = [`${id}-source`, problem ? `${id}-problem` : '']
                    .filter(Boolean)
                    .join(' ');
                  const isPending =
                    pending !== null &&
                    pending.kind !== 'entry' &&
                    pending.key === row.key &&
                    pending.field === field;
                  return (
                    <div key={field} className="field min-w-0 sm:w-40" data-testid="wire-field">
                      <label htmlFor={id} className="field-label">
                        {fieldName(field)}
                      </label>
                      <input
                        id={id}
                        className="input w-full"
                        dir={field === 'cross_section_mm2' ? 'ltr' : 'auto'}
                        inputMode={field === 'cross_section_mm2' ? 'decimal' : undefined}
                        value={shown(v)}
                        readOnly={!editable}
                        aria-readonly={!editable || undefined}
                        aria-invalid={problem !== undefined || undefined}
                        aria-describedby={described}
                        onChange={(event) => {
                          const text = event.target.value;
                          if (v.kind === 'unset' && text === '') return;
                          onChange(setUserValue(value, binding.id, row.key, field, text));
                        }}
                        data-testid={`wire-${field}`}
                      />
                      <p id={`${id}-source`} className="text-xs text-text-muted" dir="auto">
                        {sourceText(v)}
                      </p>
                      {problem !== undefined && (
                        <p
                          id={`${id}-problem`}
                          className="text-xs font-semibold"
                          data-testid="wire-problem"
                        >
                          {t(`wires.problem.${problem}`)}
                        </p>
                      )}
                      {v.kind === 'given' && v.source !== 'user' && !isPending && (
                        <button
                          type="button"
                          className={`${BUTTON} btn-secondary self-start`}
                          aria-label={t('wires.replaceNamed', {
                            field: fieldName(field),
                            wire: name,
                          })}
                          onClick={() => {
                            setPending({ kind: 'replace', key: row.key, field });
                          }}
                          data-testid="wire-replace"
                        >
                          {t('wires.replace')}
                        </button>
                      )}
                      {v.kind !== 'unset' && !isPending && (
                        <button
                          type="button"
                          className={`${BUTTON} btn-danger self-start`}
                          aria-label={t('wires.removeFieldNamed', {
                            field: fieldName(field),
                            wire: name,
                          })}
                          onClick={() => {
                            setPending({ kind: 'field', key: row.key, field });
                          }}
                          data-testid="wire-remove-field"
                        >
                          {t('wires.removeField')}
                        </button>
                      )}
                      {isPending &&
                        pending.kind === 'replace' &&
                        confirmBox(
                          t('wires.replaceConfirm', {
                            field: fieldName(field),
                            value: shown(v),
                            source: sourceText(v),
                          }),
                          t('wires.replaceAction'),
                          () => {
                            onChange(removeWireValue(value, binding.id, row.key, field));
                            setPending(null);
                            announce(t('wires.replaced', { field: fieldName(field), wire: name }));
                            focusLater(id);
                          },
                          'wire-replace-confirm',
                        )}
                      {isPending &&
                        pending.kind === 'field' &&
                        confirmBox(
                          t('wires.removeFieldConfirm', {
                            field: fieldName(field),
                            value: shown(v) || '—',
                            source: sourceText(v),
                          }),
                          t('wires.removeAction'),
                          () => {
                            onChange(removeWireValue(value, binding.id, row.key, field));
                            setPending(null);
                            announce(
                              t('wires.removedField', { field: fieldName(field), wire: name }),
                            );
                            focusLater(id);
                          },
                          'wire-remove-field-confirm',
                        )}
                    </div>
                  );
                })}
              </div>
              {row.otherKeys.map((other) => {
                const isPending =
                  pending?.kind === 'field' && pending.key === row.key && pending.field === other;
                return (
                  <div key={other} className="flex min-w-0 flex-col gap-1" data-testid="wire-extra">
                    <p className="text-xs font-semibold" dir="auto">
                      {t('wires.problem.extra_field', { name: other })}
                    </p>
                    {isPending ? (
                      confirmBox(
                        t('wires.removeFieldConfirm', {
                          field: other,
                          value: '—',
                          source: t('wires.source.unreadable'),
                        }),
                        t('wires.removeAction'),
                        () => {
                          onChange(removeWireValue(value, binding.id, row.key, other));
                          setPending(null);
                          announce(t('wires.removedField', { field: other, wire: name }));
                          focusLater(rowId(index));
                        },
                        'wire-remove-field-confirm',
                      )
                    ) : (
                      <button
                        type="button"
                        className={`${BUTTON} btn-danger self-start`}
                        onClick={() => {
                          setPending({ kind: 'field', key: row.key, field: other });
                        }}
                        id={`${rowId(index)}-extra-${other.replace(/[^A-Za-z0-9_-]/g, '_')}`}
                        data-testid="wire-remove-extra"
                      >
                        {t('wires.removeExtra', { name: other })}
                      </button>
                    )}
                  </div>
                );
              })}
              {anyGiven &&
                (pending?.kind === 'entry' && pending.key === row.key ? (
                  confirmBox(
                    t('wires.removeEntryConfirm', {
                      wire: name,
                      // The stored entry as it is, when it cannot be read field by field.
                      values:
                        row.unreadableEntry ??
                        WIRE_FIELDS.filter((f) => row.values[f].kind !== 'unset')
                          .map((f) => `${fieldName(f)}: ${shown(row.values[f]) || '—'}`)
                          .concat(row.otherKeys)
                          .join('; '),
                    }),
                    t('wires.removeAction'),
                    () => {
                      onChange(removeWireEntry(value, binding.id, row.key));
                      setPending(null);
                      announce(t('wires.removedEntry', { wire: name }));
                      focusLater(fieldId(index, 'number'));
                    },
                    'wire-remove-entry-confirm',
                  )
                ) : (
                  <button
                    type="button"
                    className={`${BUTTON} btn-danger self-start`}
                    aria-label={t('wires.removeEntryNamed', { wire: name })}
                    onClick={() => {
                      setPending({ kind: 'entry', key: row.key });
                    }}
                    id={row.unreadableEntry !== null ? `${rowId(index)}-unreadable` : undefined}
                    data-testid="wire-remove-entry"
                  >
                    {t('wires.removeEntry')}
                  </button>
                ))}
            </li>
          );
        })}
      </ul>
      {problems.stray.map((key) => {
        const isPending = pending?.kind === 'entry' && pending.key === key;
        return (
          <div key={key} className="flex min-w-0 flex-col gap-1" data-testid="wire-stray">
            <p className="text-xs font-semibold" dir="auto">
              {t('wires.problem.stray', { key })}
            </p>
            {isPending ? (
              confirmBox(
                t('wires.removeStrayConfirm', { key }),
                t('wires.removeAction'),
                () => {
                  onChange(removeWireEntry(value, binding.id, key));
                  setPending(null);
                  announce(t('wires.removedStray', { key }));
                  focusLater(`${base}-wires-title`);
                },
                'wire-remove-stray-confirm',
              )
            ) : (
              <button
                type="button"
                className={`${BUTTON} btn-danger self-start`}
                onClick={() => {
                  setPending({ kind: 'entry', key });
                }}
                id={`${base}-stray-${key.replace(/[^A-Za-z0-9_-]/g, '_')}`}
                data-testid="wire-remove-stray"
              >
                {t('wires.removeStray', { key })}
              </button>
            )}
          </div>
        );
      })}
    </section>
  );
}
