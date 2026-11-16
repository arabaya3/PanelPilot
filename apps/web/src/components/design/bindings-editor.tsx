'use client';

/**
 * M9b1: the guided editor of pattern bindings, with a preview the server
 * composes (docs/architecture/m9b1-guided-bindings.md). A binding says which
 * components fill a pattern's slots, which strips play its strip roles, each
 * point's marking and, where a connection needs one, the cable. Nothing is
 * inferred. A selection that no longer resolves stays shown as such; nothing is
 * cleared or replaced. The preview is unsaved and review only: it calls the
 * read-only validate endpoint and shows this binding's rows by the ids the
 * composer derives; the browser computes no wiring.
 */
import { useEffect, useId, useRef, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import {
  addBinding,
  bindablePatterns,
  bindingIdProblem,
  bindingProblems,
  bindingRowIds,
  bindingState,
  componentChoices,
  removeBinding,
  removeExtra,
  setRole,
  setSlot,
  stripChoices,
  updateBinding,
  type BindingProblem,
  type BoardChoices,
} from '@/components/design/binding-model';
import {
  bindingPoints,
  bindingsOf,
  type ConfigBinding,
  type Configuration,
  type ConflictTarget,
  designationText,
  setMarking,
} from '@/components/design/configuration-model';
import type { ConfigurationOutcome, V2ModelSummary } from '@/lib/design';

export type PreviewFn = (configuration: Configuration) => Promise<ConfigurationOutcome>;

function focusLater(id: string) {
  window.setTimeout(() => {
    document.getElementById(id)?.focus();
  }, 0);
}

function wrapButton(tone: 'secondary' | 'danger') {
  return `btn btn-sm btn-${tone} max-w-full whitespace-normal text-start`;
}

function Problem({ id, text }: { id: string; text: string }) {
  return (
    <p id={id} className="text-xs font-semibold" data-testid="binding-problem">
      {text}
    </p>
  );
}

type PreviewState =
  | { kind: 'idle' }
  | { kind: 'loading'; forText: string }
  | { kind: 'shown'; forText: string; model: V2ModelSummary }
  | { kind: 'refused'; forText: string; message: string };

/** The rows of a composed model that belong to one binding. */
function rowsOf(model: V2ModelSummary, ids: ReturnType<typeof bindingRowIds>) {
  const connections = model.connections.filter((connection) => ids.connections.has(connection.id));
  const nets = new Set([...ids.nets, ...connections.map((connection) => connection.net_id)]);
  return {
    nets: model.nets
      .filter((net) => nets.has(net.id))
      .map((net) => ({ net, shared: ids.sharedNets.has(net.id) })),
    connections,
    wires: model.wires.filter((wire) => ids.wires.has(wire.wire_id)),
    points: model.strips.flatMap((strip) =>
      strip.points
        .filter((point) => ids.points.has(point.id))
        .map((point) => ({ strip: strip.designation ?? strip.id, point })),
    ),
  };
}

function BindingPreview({
  value,
  binding,
  preview,
  base,
  onUnauthorized,
  savedRevision,
}: {
  value: Configuration;
  binding: ConfigBinding;
  preview: PreviewFn;
  base: string;
  savedRevision: number;
  onUnauthorized?: (() => void) | undefined;
}) {
  const t = useIsolatingTranslations('configuration');
  const [state, setState] = useState<PreviewState>({ kind: 'idle' });
  // Only the latest request may set what is shown: an older answer is dropped.
  const latest = useRef(0);
  // A preview answers this configuration against the latest saved revision (its load
  // schedule): a newer saved revision makes it outdated as much as an edit does.
  const text = `${String(savedRevision)}|${JSON.stringify(value)}`;
  const outdated = state.kind !== 'idle' && state.kind !== 'loading' && state.forText !== text;

  async function run() {
    const mine = latest.current + 1;
    latest.current = mine;
    const forText = text;
    setState({ kind: 'loading', forText });
    const result = await preview(value);
    if (latest.current !== mine) return;
    if (result.kind === 'reviewed')
      setState({ kind: 'shown', forText, model: result.review.model });
    else {
      // Said by the server's code (and the reference it names), never guessed here.
      const message =
        result.kind === 'refused'
          ? [result.params?.refusal ?? result.code ?? '', result.params?.ref ?? '']
              .filter((part) => part !== '')
              .join('; ')
          : '';
      if (result.kind === 'unauthorized') onUnauthorized?.();
      setState({
        kind: 'refused',
        forText,
        message:
          result.kind === 'refused'
            ? message || '—'
            : t(
                result.kind === 'unauthorized'
                  ? 'bindings.previewSignIn'
                  : 'bindings.previewFailed',
              ),
      });
    }
  }

  const ids = bindingRowIds(value, binding);
  const rows = state.kind === 'shown' ? rowsOf(state.model, ids) : null;

  return (
    <section
      className="flex min-w-0 flex-col gap-2 rounded-md border border-border-subtle p-3"
      aria-labelledby={`${base}-preview-title`}
      data-testid="binding-preview"
    >
      <h4 id={`${base}-preview-title`} className="text-sm font-semibold">
        {t('bindings.previewTitle')}
      </h4>
      <p className="text-xs text-text-muted" data-testid="binding-preview-label">
        {t('bindings.previewLabel')}
      </p>
      <button
        type="button"
        className={wrapButton('secondary') + ' self-start'}
        disabled={state.kind === 'loading'}
        onClick={() => {
          void run();
        }}
        data-testid="binding-preview-run"
      >
        {state.kind === 'idle' ? t('bindings.previewRun') : t('bindings.previewRefresh')}
      </button>
      <div role="status" aria-live="polite" className="flex min-w-0 flex-col gap-2">
        {state.kind === 'loading' && <p className="text-xs">{t('bindings.previewLoading')}</p>}
        {outdated && (
          <p className="text-xs font-semibold" data-testid="binding-preview-outdated">
            {t('bindings.previewOutdated')}
          </p>
        )}
        {state.kind === 'refused' && (
          <p className="text-xs font-semibold" data-testid="binding-preview-refused">
            {t('bindings.previewRefused', { reason: state.message })}
          </p>
        )}
      </div>
      {rows !== null && (
        <div className="flex min-w-0 flex-col gap-2 text-sm" data-testid="binding-preview-rows">
          <h5 className="text-xs font-semibold">
            {t('bindings.previewNets', { count: String(rows.nets.length) })}
          </h5>
          <ul className="flex min-w-0 flex-col gap-1" data-testid="binding-preview-nets">
            {rows.nets.map(({ net, shared }) => (
              <li key={net.id} className="break-words" dir="auto">
                {t(shared ? 'bindings.previewNetShared' : 'bindings.previewNet', {
                  signal: net.signal,
                  members: net.members.join(', '),
                })}
              </li>
            ))}
          </ul>
          <h5 className="text-xs font-semibold">
            {t('bindings.previewConnections', { count: String(rows.connections.length) })}
          </h5>
          <ul className="flex min-w-0 flex-col gap-1" data-testid="binding-preview-connections">
            {rows.connections.map((connection) => (
              <li key={connection.id} className="break-words" dir="auto">
                {t('bindings.previewConnection', {
                  start: connection.start,
                  end: connection.end,
                  via: t(`bindings.via.${connection.via}`),
                })}
              </li>
            ))}
          </ul>
          <h5 className="text-xs font-semibold">
            {t('bindings.previewWires', { count: String(rows.wires.length) })}
          </h5>
          <ul className="flex min-w-0 flex-col gap-1" data-testid="binding-preview-wires">
            {rows.wires.map((wire) => (
              <li key={wire.wire_id} className="break-words" dir="auto">
                {t('bindings.previewWire', {
                  start: wire.start,
                  end: wire.end,
                  signal: wire.signal,
                })}
              </li>
            ))}
          </ul>
          <h5 className="text-xs font-semibold">
            {t('bindings.previewPoints', { count: String(rows.points.length) })}
          </h5>
          <ul className="flex min-w-0 flex-col gap-1" data-testid="binding-preview-points">
            {rows.points.map(({ strip, point }) => (
              <li key={point.id} className="break-words" dir="auto">
                {t('bindings.previewPoint', {
                  strip,
                  marking: point.marking,
                  position: String(point.position),
                  level: String(point.level),
                })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function BindingRow({
  value,
  onChange,
  binding,
  rowId,
  boards,
  boardChoices,
  conflict,
  preview,
  announce,
  addFieldId,
  onUnauthorized,
  savedRevision,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  binding: ConfigBinding;
  rowId: string;
  boards: string[];
  boardChoices: BoardChoices[] | null;
  conflict: ConflictTarget | null;
  preview: PreviewFn | null;
  announce: (message: string) => void;
  addFieldId: string;
  onUnauthorized?: (() => void) | undefined;
  savedRevision: number;
}) {
  const t = useIsolatingTranslations('configuration');
  const pattern = bindablePatterns(value).find((item) => item.ref === binding.pattern);
  const state = bindingState(binding);
  const problems = bindingProblems(value, binding, boardChoices);
  const board = boardChoices?.find((item) => item.id === binding.board);
  const problemOf = (field: BindingProblem['field'], key?: string) =>
    problems.find((p) => p.field === field && (!('key' in p) || p.key === key));
  const conflicted =
    conflict !== null && conflict.kind === 'binding' && conflict.entry === binding.id;
  const points = bindingPoints(value, binding);
  const strips = stripChoices(value, binding.board);

  function select(
    fieldId: string,
    label: string,
    current: string,
    options: { id: string; label: string }[],
    onPick: (next: string) => void,
    problem: BindingProblem | undefined,
    testId: string,
    noneLabel: string,
  ) {
    const known = current === '' || options.some((option) => option.id === current);
    return (
      <div className="field min-w-0 flex-1">
        <label htmlFor={fieldId} className="field-label">
          {label}
        </label>
        <select
          id={fieldId}
          className="input w-full"
          dir="auto"
          value={current}
          aria-invalid={problem !== undefined || undefined}
          aria-describedby={problem ? `${fieldId}-problem` : undefined}
          onChange={(event) => {
            onPick(event.target.value);
          }}
          data-testid={testId}
        >
          <option value="">{noneLabel}</option>
          {!known && <option value={current}>{t('bindings.goneOption', { id: current })}</option>}
          {options.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
        {problem !== undefined && (
          <Problem id={`${fieldId}-problem`} text={t(`bindings.problem.${problem.code}`)} />
        )}
      </div>
    );
  }

  return (
    <li
      id={rowId}
      tabIndex={-1}
      className={`flex min-w-0 flex-col gap-3 rounded-md border p-3 ${
        conflicted ? 'border-status-critical' : 'border-border-subtle'
      }`}
      data-testid="binding-entry"
      data-conflict={conflicted || undefined}
    >
      <p className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold" dir="ltr" data-testid="binding-id">
          {binding.id}
        </span>
        <span>{t('bindings.applies', { pattern: binding.pattern })}</span>
      </p>
      {conflicted && (
        <p className="text-xs font-semibold" role="alert" data-testid="binding-refused">
          {t('bindings.refused', {
            reason: conflict.reason || '—',
            ref: conflict.ref || '—',
          })}
        </p>
      )}
      {problems.length > 0 && (
        <p className="text-xs font-semibold" data-testid="binding-problems">
          {t('bindings.problems', { count: String(problems.length) })}
        </p>
      )}
      {pattern === undefined ? (
        <Problem id={`${rowId}-pattern-problem`} text={t('bindings.problem.no_pattern')} />
      ) : (
        <>
          <div className="flex min-w-0 flex-wrap items-start gap-2">
            {select(
              `${rowId}-board`,
              t('bindings.board'),
              binding.board,
              boards.map((name) => ({ id: name, label: name })),
              (next) => {
                if (next !== '') onChange(updateBinding(value, binding.id, { board: next }));
              },
              problemOf('board'),
              'binding-board',
              t('bindings.chooseBoard'),
            )}
            {select(
              `${rowId}-circuit`,
              t('bindings.circuit'),
              binding.circuit ?? '',
              board?.circuits ?? [],
              (next) => {
                onChange(updateBinding(value, binding.id, { circuit: next === '' ? null : next }));
              },
              problemOf('circuit'),
              'binding-circuit',
              t('bindings.noCircuit'),
            )}
          </div>

          <p className="text-xs text-text-muted" data-testid="binding-circuit-help">
            {boardChoices === null ? t('bindings.designFirst') : t('bindings.circuitHelp')}
          </p>
          {problems.some((problem) => problem.field === 'extra') && (
            <ul className="flex min-w-0 flex-col gap-1" data-testid="binding-extras">
              {problems.flatMap((problem) =>
                problem.field === 'extra'
                  ? [
                      <li
                        key={`${problem.code}-${problem.key}`}
                        className="flex min-w-0 flex-wrap items-center gap-2"
                      >
                        <span className="text-xs font-semibold" data-testid="binding-extra">
                          {t(`bindings.problem.${problem.code}`, { key: problem.key })}
                        </span>
                        <button
                          type="button"
                          className={wrapButton('secondary')}
                          onClick={() => {
                            onChange(removeExtra(value, binding.id, problem.code, problem.key));
                            announce(t('bindings.extraRemoved', { key: problem.key }));
                          }}
                          data-testid="binding-extra-remove"
                        >
                          {t('bindings.extraRemove', { key: problem.key })}
                        </button>
                      </li>,
                    ]
                  : [],
              )}
            </ul>
          )}
          <fieldset className="flex min-w-0 flex-col gap-2" data-testid="binding-slots">
            <legend className="text-sm font-semibold">{t('bindings.slots')}</legend>
            {pattern.slots.length === 0 && (
              <p className="text-xs text-text-muted">{t('bindings.noSlots')}</p>
            )}
            {pattern.slots.map((slot) => {
              const given = state.slots[slot.id];
              if (given !== undefined && !('component' in given)) {
                return (
                  <p key={slot.id} className="text-xs" data-testid="binding-slot-kept">
                    {t('bindings.slotKept', { slot: slot.id })}
                  </p>
                );
              }
              return (
                <div key={slot.id} data-testid="binding-slot">
                  {select(
                    `${rowId}-slot-${slot.id}`,
                    t('bindings.slotLabel', {
                      slot: slot.id,
                      template: slot.template ?? t('bindings.anyTemplate'),
                    }),
                    given?.component ?? '',
                    componentChoices(value, binding.board, slot.template),
                    (next) => {
                      onChange(setSlot(value, binding.id, slot.id, next === '' ? null : next));
                    },
                    problemOf('slot', slot.id),
                    'binding-slot-component',
                    slot.deviceKind !== null && binding.circuit !== null
                      ? t('bindings.fromCircuit')
                      : t('bindings.notChosen'),
                  )}
                </div>
              );
            })}
          </fieldset>

          <fieldset className="flex min-w-0 flex-col gap-2" data-testid="binding-roles">
            <legend className="text-sm font-semibold">{t('bindings.roles')}</legend>
            {pattern.roles.length === 0 && (
              <p className="text-xs text-text-muted">{t('bindings.noRoles')}</p>
            )}
            {pattern.roles.map((role) => (
              <div key={role} data-testid="binding-role">
                {select(
                  `${rowId}-role-${role}`,
                  t('bindings.roleLabel', { role }),
                  state.strips[role] ?? '',
                  strips,
                  (next) => {
                    onChange(setRole(value, binding.id, role, next === '' ? null : next));
                  },
                  problemOf('role', role),
                  'binding-role-strip',
                  t('bindings.notChosen'),
                )}
              </div>
            ))}
          </fieldset>

          <fieldset className="flex min-w-0 flex-col gap-2" data-testid="binding-markings">
            <legend className="text-sm font-semibold">{t('editor.points')}</legend>
            {points.length === 0 && (
              <p className="text-xs text-text-muted">{t('editor.noPoints')}</p>
            )}
            {points.map((point) => {
              const fieldId = `${rowId}-point-${point.id}`;
              const problem = problemOf('marking', point.id);
              return (
                <div key={point.id} className="field min-w-0">
                  <label htmlFor={fieldId} className="field-label">
                    {t('editor.pointMarking', {
                      point: point.id,
                      strip: point.strip === '' ? '—' : point.strip,
                    })}
                  </label>
                  <input
                    id={fieldId}
                    className="input w-full"
                    dir="auto"
                    maxLength={50}
                    value={point.marking}
                    placeholder={t('notGivenPlaceholder')}
                    aria-invalid={problem !== undefined || undefined}
                    aria-describedby={problem ? `${fieldId}-problem` : undefined}
                    onChange={(event) => {
                      onChange(setMarking(value, binding.id, point.id, event.target.value));
                    }}
                    data-testid="point-marking"
                  />
                  {problem !== undefined && (
                    <Problem id={`${fieldId}-problem`} text={t('bindings.problem.missing')} />
                  )}
                </div>
              );
            })}
          </fieldset>

          {(pattern.needsCable || state.cable !== null) && (
            <div className="flex min-w-0 flex-wrap items-start gap-2">
              {select(
                `${rowId}-cable`,
                t('bindings.cable'),
                state.cable ?? '',
                board?.cables ?? [],
                (next) => {
                  onChange(updateBinding(value, binding.id, { cable: next === '' ? null : next }));
                },
                problemOf('cable'),
                'binding-cable',
                t('bindings.noCable'),
              )}
            </div>
          )}

          {preview !== null && (
            <BindingPreview
              value={value}
              binding={binding}
              preview={preview}
              base={rowId}
              onUnauthorized={onUnauthorized}
              savedRevision={savedRevision}
            />
          )}
        </>
      )}
      <button
        type="button"
        className={wrapButton('danger') + ' self-start'}
        aria-label={t('bindings.removeNamed', { id: binding.id })}
        onClick={() => {
          onChange(removeBinding(value, binding.id));
          announce(t('bindings.removed', { id: binding.id }));
          focusLater(addFieldId);
        }}
        data-testid="binding-remove"
      >
        {t('editor.removeBinding')}
      </button>
    </li>
  );
}

export function BindingsEditor({
  value,
  onChange,
  boards,
  boardChoices,
  conflict,
  preview,
  rowIdOf,
  headingId,
  onUnauthorized,
  savedRevision = 0,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  /** The board names of the form as it stands. */
  boards: string[];
  /** Each board's circuits and cables from the opened design; null when not known. */
  boardChoices: BoardChoices[] | null;
  conflict: ConflictTarget | null;
  /** The server's read-only composition of a configuration; null where none can be asked for. */
  preview: PreviewFn | null;
  rowIdOf: (id: string) => string;
  headingId: string;
  /** Called when a preview finds the session expired. */
  onUnauthorized?: (() => void) | undefined;
  /** The latest saved revision, whose load schedule a preview is composed with. */
  savedRevision?: number;
}) {
  const t = useIsolatingTranslations('configuration');
  const id = useId();
  const bindings = bindingsOf(value);
  const patterns = bindablePatterns(value);
  const [draft, setDraft] = useState({ id: '', pattern: '', board: '', circuit: '' });
  const [status, setStatus] = useState('');
  const idProblem = draft.id === '' ? null : bindingIdProblem(value, draft.id);
  const pattern = draft.pattern !== '' ? draft.pattern : (patterns[0]?.ref ?? '');
  const board = draft.board !== '' ? draft.board : (boards[0] ?? '');
  const circuits = boardChoices?.find((item) => item.id === board)?.circuits ?? [];
  const addId = `${id}-add-id`;

  useEffect(() => {
    // A circuit chosen for another board is not carried over.
    setDraft((current) => (current.circuit === '' ? current : { ...current, circuit: '' }));
  }, [board]);

  return (
    <section
      className="flex min-w-0 flex-col gap-3"
      aria-labelledby={headingId}
      data-testid="bindings-editor"
    >
      <h3 id={headingId} className="text-base font-semibold">
        {t('editor.bindings')}
      </h3>
      <p className="text-sm text-text-muted">{t('bindings.intro')}</p>
      <p className="sr-only" role="status" aria-live="polite" data-testid="bindings-status">
        {status}
      </p>
      {bindings.length === 0 ? (
        <p className="text-sm text-text-muted">{t('editor.noBindings')}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {bindings.map((binding) => (
            <BindingRow
              key={binding.id}
              value={value}
              onChange={onChange}
              binding={binding}
              rowId={rowIdOf(binding.id)}
              boards={boards}
              boardChoices={boardChoices}
              conflict={conflict}
              preview={preview}
              announce={setStatus}
              addFieldId={addId}
              onUnauthorized={onUnauthorized}
              savedRevision={savedRevision}
            />
          ))}
        </ul>
      )}
      {patterns.length === 0 ? (
        <p className="text-xs text-text-muted" data-testid="bindings-need-pattern">
          {t('bindings.needPattern')}
        </p>
      ) : (
        <fieldset className="flex min-w-0 flex-col gap-2" data-testid="add-binding">
          <legend className="text-sm font-semibold">{t('bindings.add')}</legend>
          <div className="flex min-w-0 flex-wrap items-end gap-2">
            <div className="field min-w-0 flex-1">
              <label htmlFor={addId} className="field-label">
                {t('bindings.id')}
              </label>
              <input
                id={addId}
                className="input w-full"
                dir="ltr"
                maxLength={100}
                value={draft.id}
                aria-invalid={idProblem !== null || undefined}
                aria-describedby={idProblem ? `${addId}-problem` : undefined}
                onChange={(event) => {
                  setDraft({ ...draft, id: event.target.value });
                }}
                data-testid="add-binding-id"
              />
            </div>
            <div className="field min-w-0 flex-1">
              <label htmlFor={`${id}-add-pattern`} className="field-label">
                {t('bindings.pattern')}
              </label>
              <select
                id={`${id}-add-pattern`}
                className="input w-full"
                dir="ltr"
                value={pattern}
                onChange={(event) => {
                  setDraft({ ...draft, pattern: event.target.value });
                }}
                data-testid="add-binding-pattern"
              >
                {patterns.map((item) => (
                  <option key={item.ref} value={item.ref}>
                    {item.ref}
                  </option>
                ))}
              </select>
            </div>
            <div className="field min-w-0 flex-1">
              <label htmlFor={`${id}-add-board`} className="field-label">
                {t('bindings.board')}
              </label>
              <select
                id={`${id}-add-board`}
                className="input w-full"
                dir="auto"
                value={board}
                onChange={(event) => {
                  setDraft({ ...draft, board: event.target.value });
                }}
                data-testid="add-binding-board"
              >
                {boards.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field min-w-0 flex-1">
              <label htmlFor={`${id}-add-circuit`} className="field-label">
                {t('bindings.circuit')}
              </label>
              <select
                id={`${id}-add-circuit`}
                className="input w-full"
                dir="auto"
                value={draft.circuit}
                onChange={(event) => {
                  setDraft({ ...draft, circuit: event.target.value });
                }}
                data-testid="add-binding-circuit"
              >
                <option value="">{t('bindings.noCircuit')}</option>
                {circuits.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className={wrapButton('secondary')}
              disabled={
                draft.id.trim() === '' || idProblem !== null || pattern === '' || board === ''
              }
              onClick={() => {
                const newId = draft.id.trim();
                onChange(
                  addBinding(value, {
                    id: newId,
                    pattern,
                    board,
                    circuit: draft.circuit === '' ? null : draft.circuit,
                  }),
                );
                setStatus(t('bindings.added', { id: newId }));
                setDraft({ ...draft, id: '' });
                focusLater(`${rowIdOf(newId)}-board`);
              }}
              data-testid="add-binding-submit"
            >
              {t('bindings.addAction')}
            </button>
          </div>
          {idProblem !== null && (
            <Problem id={`${addId}-problem`} text={t(`bindings.idProblem.${idProblem}`)} />
          )}
          {boardChoices === null && (
            <p className="text-xs text-text-muted" data-testid="add-binding-design-first">
              {t('bindings.designFirst')}
            </p>
          )}
        </fieldset>
      )}
    </section>
  );
}

/** A component's name for a binding's slot, as the editor shows it. */
export function componentLabel(config: Configuration, id: string): string {
  const found = (Array.isArray(config.components) ? (config.components as unknown[]) : []).find(
    (item) => (item as { id?: unknown }).id === id,
  ) as { designation?: unknown } | undefined;
  return found ? designationText(found.designation) || id : id;
}
