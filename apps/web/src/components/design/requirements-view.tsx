'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import { LoadRows } from '@/components/design/load-rows';
import { useOutcomeText } from '@/components/design/note-text';
import type { BoardForm, Load } from '@/components/design/schedule';
import { PlusIcon } from '@/components/icons';
import { Notice, Panel, StatusBadge, TableFrame, type Tone } from '@/components/ui';
import {
  answerQuestion,
  CONTROL_NEEDS,
  previewRequirements,
  type ControlNeed,
  type DesignFinding,
  type ProjectDesignRequest,
  type RequirementQuestion,
  type RequirementsInput,
  type RequirementsRecord,
  type RequirementSubject,
  type SavedProject,
} from '@/lib/design';

/** Each load's control needs, by the load row's key. Requirements only: nothing is designed from them. */
export type ControlNeeds = Readonly<Record<number, readonly ControlNeed[]>>;

/** What the guided intake records with a save: the intake and each load's control needs. */
export function requirementsInputOf(
  boards: readonly BoardForm[],
  control: ControlNeeds,
): RequirementsInput {
  return {
    intake: 'guided',
    control: boards.flatMap((board, boardIndex) =>
      board.loads.flatMap((load, index) => {
        const needs = CONTROL_NEEDS.filter((need) => control[load.key]?.includes(need));
        return needs.length > 0 ? [{ board_index: boardIndex, load_index: index, needs }] : [];
      }),
    ),
  };
}

/** The control needs a saved record holds, by the load keys of the boards it opened into. */
export function controlNeedsFrom(
  record: RequirementsRecord | null,
  boards: readonly BoardForm[],
): Record<number, ControlNeed[]> {
  const needs: Record<number, ControlNeed[]> = {};
  if (record === null) return needs;
  for (const entry of record.control) {
    const boardName = record.boards.find((board) => board.ref === entry.board_ref)?.name;
    const board = boards.find((candidate) => candidate.name === boardName);
    const loadsOfBoard = record.loads.filter((load) => load.board_ref === entry.board_ref);
    const index = loadsOfBoard.findIndex((load) => load.ref === entry.load_ref);
    const load = index >= 0 ? board?.loads[index] : undefined;
    if (load === undefined) continue;
    needs[load.key] = CONTROL_NEEDS.filter((need) => entry.needs.includes(need));
  }
  return needs;
}

/** Board fields an answer to a question fills in on the form, before the project is saved. */
const BOARD_ANSWERS: Readonly<Record<string, 'faultLevel' | 'earthLoop' | 'feederLength'>> = {
  fault_level_ka: 'faultLevel',
  ze_ohm: 'earthLoop',
  earth_loop_ohm: 'earthLoop',
  feeder_length_m: 'feederLength',
};

/** Fields a value can be given for (the rest are acknowledge-only, e.g. motor nameplate current). */
const ANSWERABLE = new Set([
  'fault_level_ka',
  'ze_ohm',
  'earth_loop_ohm',
  'feeder_length_m',
  'power_factor',
]);

/** A question's field, by its label in `design.field` or `design.requirements.field`. */
const FIELD_LABELS: Readonly<Record<string, string>> = {
  fault_level_ka: 'field.faultLevel',
  earth_loop_ohm: 'field.earthLoop',
  ze_ohm: 'field.earthLoop',
  feeder_length_m: 'field.feederLength',
  power_factor: 'field.powerFactor',
  length_m: 'field.length',
};

const STATE_TONE: Record<RequirementQuestion['state'], Tone> = {
  open: 'warning',
  answered: 'success',
  acknowledged: 'info',
};

type Impls = { preview?: typeof previewRequirements; answer?: typeof answerQuestion };

/**
 * The guided requirements step: boards (name, location, fed from), each
 * board's loads, each load's control needs, then the open items and
 * questions the server derives, and which requirement produced each circuit
 * and feeder. Nothing here claims the design complete or compliant: control
 * needs are recorded, not designed; an acknowledged assumption is not
 * engineer-validated; board-to-board wiring is not modelled.
 */
export function RequirementsView({
  token,
  boards,
  onBoardChange,
  onAddBoard,
  onLoadChange,
  onLoadRemove,
  onLoadAdd,
  control,
  onControlChange,
  request,
  opened,
  savedRecord,
  findings = [],
  onProject,
  onUnauthorized,
  dirty = false,
  impls = {},
}: {
  token: string | null;
  boards: readonly BoardForm[];
  onBoardChange: (key: number, patch: Partial<BoardForm>) => void;
  onAddBoard: () => void;
  onLoadChange: (boardKey: number, loadKey: number, patch: Partial<Load>) => void;
  onLoadRemove: (boardKey: number, loadKey: number) => void;
  onLoadAdd: (boardKey: number) => void;
  control: ControlNeeds;
  onControlChange: (loadKey: number, needs: ControlNeed[]) => void;
  /** The request as entered now, or null while the form is incomplete. */
  request: ProjectDesignRequest | null;
  /** The saved revision open, if any: answers and acknowledgements are recorded against it. */
  opened: { id: string; revision: number } | null;
  /** The record the server stored with the revision open (or derived for an older one). */
  savedRecord: RequirementsRecord | null;
  /** The revision's findings, for the blocking ones this step repeats (a duplicate designation). */
  findings?: readonly DesignFinding[];
  /**
   * An answer or acknowledgement came back with the project as now stored. An
   * answer changed the inputs (a new revision), so the form takes them.
   */
  onProject: (project: SavedProject, action: 'answer' | 'acknowledge') => void;
  /** Unsaved changes: an answer would make a revision without them, so it waits for a save. */
  dirty?: boolean;
  onUnauthorized?: () => void;
  impls?: Impls;
}) {
  const t = useTranslations('design.requirements');
  const td = useTranslations('design');
  const say = useOutcomeText();
  const id = useId();
  const { preview = previewRequirements, answer = answerQuestion } = impls;
  const [previewed, setPreviewed] = useState<RequirementsRecord | null>(null);
  const [previewFindings, setPreviewFindings] = useState<DesignFinding[]>([]);
  // An acknowledgement's record as the server answered it, until the revision is read again.
  const [acknowledged, setAcknowledged] = useState<RequirementsRecord | null>(null);
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const sequence = useRef(0);

  // A record the server stored (a save, an answer, an opening) replaces any preview.
  useEffect(() => {
    setPreviewed(null);
    setPreviewFindings([]);
    setAcknowledged(null);
  }, [savedRecord]);

  const record = previewed ?? acknowledged ?? savedRecord;
  const boardNames = boards.map((board) => board.name.trim()).filter((name) => name !== '');
  const duplicates = (previewed !== null ? previewFindings : findings).filter(
    (finding) => finding.code === 'designation_duplicate',
  );

  function boardOfRef(ref: string | null | undefined): string | null {
    if (!ref || record === null) return null;
    return record.boards.find((board) => board.ref === ref)?.name ?? null;
  }
  function loadOfRef(ref: string | null | undefined): string | null {
    if (!ref || record === null) return null;
    return record.loads.find((load) => load.ref === ref)?.description ?? null;
  }
  function subjectText(subject: RequirementSubject): string {
    const board = boardOfRef(subject.board_ref);
    const load = loadOfRef(subject.load_ref);
    return [board, load].filter((part): part is string => part !== null && part !== '').join(' › ');
  }
  function fieldLabel(field: string | null): string {
    if (field === null) return '';
    const key = FIELD_LABELS[field];
    if (key && td.has(key)) return td(key);
    if (t.has(`field.${field}`)) return t(`field.${field}`);
    return field;
  }
  /** What a requirement or design element is called, for the trace. */
  function refText(ref: string): string {
    if (record === null) return ref;
    const load = record.loads.find((entry) => entry.ref === ref);
    if (load) return `${boardOfRef(load.board_ref) ?? ''} › ${load.description}`;
    const feeder = record.feeders.find((entry) => entry.ref === ref);
    if (feeder) {
      return t('trace.feeder', {
        board: boardOfRef(feeder.board_ref) ?? '',
        from: boardOfRef(feeder.supply_board_ref) ?? '',
      });
    }
    const board = record.boards.find((entry) => entry.ref === ref);
    if (board) {
      const from = boardOfRef(board.fed_from_ref);
      return from
        ? t('trace.feeder', { board: board.name, from })
        : t('trace.board', { board: board.name });
    }
    return ref;
  }

  async function check() {
    if (token === null || request === null || checking) return;
    const ticket = ++sequence.current;
    setChecking(true);
    setMessage(null);
    const outcome = await preview({
      token,
      request,
      requirementsInput: requirementsInputOf(boards, control),
    });
    if (ticket !== sequence.current) return;
    setChecking(false);
    if (outcome.kind === 'previewed') {
      setPreviewed(outcome.requirements);
      setPreviewFindings(outcome.findings);
      return;
    }
    if (outcome.kind === 'unauthorized') onUnauthorized?.();
    setMessage(say(outcome));
  }

  async function record_(question: RequirementQuestion, action: 'answer' | 'acknowledge') {
    const value = (drafts[question.id] ?? '').trim();
    if (action === 'answer' && value === '') return;
    if (opened === null) {
      // Not saved yet: a board value is typed into the form, and the server
      // asks again (or not) on the next check. Nothing is recorded as answered.
      const field = BOARD_ANSWERS[question.field ?? ''];
      const name = boardOfRef(question.subject.board_ref);
      const board = boards.find((entry) => entry.name.trim() === name);
      if (action !== 'answer' || !board) return;
      if (field) {
        onBoardChange(board.key, { [field]: value });
      } else if (question.field === 'power_factor') {
        const load = loadKeyOf(question.subject, board);
        if (load === null) return;
        onLoadChange(board.key, load, { powerFactor: value });
      } else return;
      setMessage(t('filledIn', { field: fieldLabel(question.field), board: board.name }));
      return;
    }
    if (token === null || working !== null) return;
    setWorking(question.id);
    setMessage(null);
    const outcome = await answer({
      token,
      id: opened.id,
      revision: opened.revision,
      question: question.id,
      action,
      ...(action === 'answer' ? { value } : {}),
    });
    setWorking(null);
    if (outcome.kind === 'recorded') {
      setAcknowledged(outcome.requirements);
      setMessage(t('acknowledgedNow', { revision: opened.revision }));
      return;
    }
    if (outcome.kind === 'saved') {
      setDrafts((current) =>
        Object.fromEntries(Object.entries(current).filter(([key]) => key !== question.id)),
      );
      setMessage(
        action === 'answer'
          ? t('answeredNow', { revision: outcome.project.revision })
          : t('acknowledgedNow', { revision: outcome.project.revision }),
      );
      onProject(outcome.project, action);
      return;
    }
    if (outcome.kind === 'unauthorized') onUnauthorized?.();
    setMessage(say(outcome));
  }

  /** The form row a question's load ref names, by its position among its board's loads. */
  function loadKeyOf(subject: RequirementSubject, board: BoardForm): number | null {
    if (record === null || !subject.load_ref) return null;
    const ofBoard = record.loads.filter((load) => load.board_ref === subject.board_ref);
    const index = ofBoard.findIndex((load) => load.ref === subject.load_ref);
    return board.loads[index]?.key ?? null;
  }

  function canAnswerUnsaved(question: RequirementQuestion): boolean {
    if (question.subject.board_ref == null) return false;
    if (BOARD_ANSWERS[question.field ?? ''] !== undefined) return true;
    return question.field === 'power_factor' && question.subject.load_ref != null;
  }

  function questionRow(question: RequirementQuestion) {
    const inputId = `${id}-q-${question.id}`;
    const subject = subjectText(question.subject);
    const unsavedOnly = opened === null && question.kind === 'missing_value';
    const answerable = question.answerable ?? ANSWERABLE.has(question.field ?? '');
    const blockedUnsaved = opened === null && !(answerable && canAnswerUnsaved(question));
    const ackBlockedUnsaved = opened === null;
    // An answer saves a new revision from the stored one; edits not saved would be left out.
    const blockedDirty = opened !== null && dirty && answerable;
    const busy = working === question.id;
    return (
      <li
        key={question.id}
        className="flex flex-col gap-2 border-b border-border py-3 last:border-b-0"
        data-testid={`question-${question.id}`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge tone={STATE_TONE[question.state]} testId={`question-state-${question.id}`}>
            {t(`state.${question.state}`)}
          </StatusBadge>
          <span className="font-medium">
            {question.kind === 'assumption'
              ? t.has(`assumption.${question.finding_code ?? ''}`)
                ? t(`assumption.${question.finding_code ?? ''}`)
                : t('assumption.generic', {
                    field: fieldLabel(question.field) || (question.finding_code ?? ''),
                  })
              : t('missing', { field: fieldLabel(question.field) })}
          </span>
          {subject && <bdi className="text-sm text-text-muted">{subject}</bdi>}
        </div>
        {question.state === 'acknowledged' && (
          <p className="text-sm" data-testid={`question-recorded-${question.id}`}>
            {t('acknowledgedBy', {
              name: question.by_name || t('someone'),
              date: (question.at ?? '').slice(0, 10),
            })}
          </p>
        )}
        {question.state === 'answered' && (
          <p className="text-sm" data-testid={`question-recorded-${question.id}`}>
            {t('answeredBy', {
              value:
                typeof question.answer === 'string' || typeof question.answer === 'number'
                  ? String(question.answer)
                  : '',
              name: question.by_name || t('someone'),
              date: (question.at ?? '').slice(0, 10),
            })}
          </p>
        )}
        {question.state === 'open' && answerable && (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <Field id={inputId} label={fieldLabel(question.field)}>
              <input
                id={inputId}
                inputMode="decimal"
                dir="ltr"
                value={drafts[question.id] ?? ''}
                disabled={blockedUnsaved || blockedDirty || busy}
                onChange={(event) => {
                  const text = event.target.value;
                  setDrafts((current) => ({ ...current, [question.id]: text }));
                }}
                className="input w-full sm:w-40"
              />
            </Field>
            <button
              type="button"
              className="btn btn-sm btn-secondary self-start sm:self-end"
              disabled={
                blockedUnsaved || blockedDirty || busy || (drafts[question.id] ?? '').trim() === ''
              }
              data-testid={`question-answer-${question.id}`}
              onClick={() => void record_(question, 'answer')}
            >
              {unsavedOnly ? t('fillIn') : t('answer')}
            </button>
          </div>
        )}
        {question.state === 'open' && question.kind === 'assumption' && (
          <div className="flex flex-col gap-1">
            <button
              type="button"
              className="btn btn-sm btn-secondary self-start"
              disabled={ackBlockedUnsaved || busy || record?.derived === true}
              data-testid={`question-acknowledge-${question.id}`}
              onClick={() => void record_(question, 'acknowledge')}
            >
              {t('acknowledge')}
            </button>
            <p className="text-xs text-text-muted">{t('acknowledgeMeans')}</p>
          </div>
        )}
        {question.state === 'open' && blockedDirty && (
          <p className="text-xs text-text-muted" data-testid={`question-dirty-${question.id}`}>
            {t('saveChangesFirst')}
          </p>
        )}
        {question.state === 'open' && question.kind === 'assumption' && record?.derived && (
          <p className="text-xs text-text-muted" data-testid={`question-derived-${question.id}`}>
            {t('derivedSaveNew')}
          </p>
        )}
        {question.state === 'open' &&
          (question.kind === 'assumption' ? ackBlockedUnsaved : blockedUnsaved) && (
            <p
              className="text-xs text-text-muted"
              data-testid={`question-save-first-${question.id}`}
            >
              {t('saveFirst')}
            </p>
          )}
      </li>
    );
  }

  return (
    <div className="flex flex-col gap-5" data-testid="requirements-view">
      <Notice tone="info" live={false} testId="requirements-scope">
        {t('scope')}
      </Notice>

      <Panel title={t('steps.boards')} testId="requirements-boards">
        <ol className="flex flex-col gap-4">
          {boards.map((board, index) => {
            const field = (name: string) => `${id}-board-${name}-${String(board.key)}`;
            const others = boardNames.filter((name) => name !== board.name.trim());
            return (
              <li
                key={board.key}
                className="grid grid-cols-1 gap-3 sm:grid-cols-3"
                data-testid={`requirements-board-${String(index)}`}
              >
                <Field id={field('name')} label={td('field.board')}>
                  <input
                    id={field('name')}
                    dir="ltr"
                    value={board.name}
                    onChange={(event) => {
                      onBoardChange(board.key, { name: event.target.value });
                    }}
                    className="input w-full"
                  />
                </Field>
                <Field id={field('location')} label={td('field.location')}>
                  <input
                    id={field('location')}
                    value={board.location}
                    onChange={(event) => {
                      onBoardChange(board.key, { location: event.target.value });
                    }}
                    className="input w-full"
                  />
                </Field>
                <Field id={field('fed')} label={td('field.fedFrom')}>
                  <select
                    id={field('fed')}
                    value={board.fedFrom}
                    onChange={(event) => {
                      onBoardChange(board.key, { fedFrom: event.target.value });
                    }}
                    className="input w-full"
                  >
                    <option value="">{td('fedFromNone')}</option>
                    {others.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                </Field>
                {board.fedFrom.trim() !== '' && (
                  <p
                    className="text-sm text-text-muted sm:col-span-3"
                    data-testid={`requirements-wiring-${String(index)}`}
                  >
                    {t('wiringNotModelled')}
                  </p>
                )}
              </li>
            );
          })}
        </ol>
        <button type="button" onClick={onAddBoard} className="btn btn-sm btn-ghost mt-3">
          <PlusIcon width="16" height="16" aria-hidden="true" />
          {td('addBoard')}
        </button>
      </Panel>

      <Panel title={t('steps.loads')} testId="requirements-loads">
        <div className="flex flex-col gap-5">
          {boards.map((board) => (
            <section key={board.key} className="flex flex-col gap-3">
              <h3 className="font-semibold">{t('loadsOf', { board: board.name.trim() || '—' })}</h3>
              <LoadRows
                idPrefix={`${id}-loads-${String(board.key)}`}
                loads={board.loads}
                onChange={(key, patch) => {
                  onLoadChange(board.key, key, patch);
                }}
                onRemove={(key) => {
                  onLoadRemove(board.key, key);
                }}
                onAdd={() => {
                  onLoadAdd(board.key);
                }}
              />
            </section>
          ))}
        </div>
      </Panel>

      <Panel title={t('steps.control')} testId="requirements-control">
        <p className="mb-3 text-sm text-text-muted">{t('controlIntro')}</p>
        <p id={`${id}-needs-controlled`} className="mb-3 text-sm text-text-muted">
          {t('needsControlled')}
        </p>
        <TableFrame label={t('steps.control')}>
          <table className="data-table w-full text-sm">
            <thead>
              <tr>
                <th className="p-2 text-start" scope="col">
                  {td('field.board')}
                </th>
                <th className="p-2 text-start" scope="col">
                  {td('field.description')}
                </th>
                {CONTROL_NEEDS.map((need) => (
                  <th key={need} className="p-2 text-start" scope="col">
                    {t(`need.${need}`)}
                  </th>
                ))}
                <th className="p-2 text-start" scope="col">
                  {t('controlStatus')}
                </th>
              </tr>
            </thead>
            <tbody>
              {boards.flatMap((board) =>
                board.loads.map((load, index) => {
                  const needs = control[load.key] ?? [];
                  const label = load.description.trim() || `#${String(index + 1)}`;
                  return (
                    <tr key={load.key} data-testid={`control-row-${String(load.key)}`}>
                      <td className="p-2" dir="ltr">
                        {board.name}
                      </td>
                      <td className="p-2">
                        <bdi>{label}</bdi>
                      </td>
                      {CONTROL_NEEDS.map((need) => (
                        <td key={need} className="p-2">
                          <input
                            type="checkbox"
                            aria-label={`${label}: ${t(`need.${need}`)}`}
                            data-testid={`control-${need}-${String(load.key)}`}
                            checked={needs.includes(need)}
                            // Recorded only for a load the PLC switches; the page
                            // never ticks "PLC switched" for the engineer.
                            disabled={!load.controlled && !needs.includes(need)}
                            aria-describedby={
                              load.controlled ? undefined : `${id}-needs-controlled`
                            }
                            onChange={(event) => {
                              const next = event.target.checked
                                ? [...needs, need]
                                : needs.filter((entry) => entry !== need);
                              onControlChange(
                                load.key,
                                CONTROL_NEEDS.filter((entry) => next.includes(entry)),
                              );
                            }}
                          />
                        </td>
                      ))}
                      <td className="p-2 text-text-muted">
                        {needs.length > 0 && !load.controlled ? (
                          <span
                            className="field-error"
                            data-testid={`control-not-controlled-${String(load.key)}`}
                          >
                            {t('needsNotControlled')}
                          </span>
                        ) : needs.length > 0 || load.controlled ? (
                          t('controlNotDesigned')
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  );
                }),
              )}
            </tbody>
          </table>
        </TableFrame>
      </Panel>

      <Panel
        title={t('steps.openItems')}
        testId="requirements-open-items"
        actions={
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={token === null || request === null || checking}
            data-testid="requirements-check"
            onClick={() => void check()}
          >
            {checking ? t('checking') : t('check')}
          </button>
        }
      >
        <div className="flex flex-col gap-3">
          {request === null && (
            <p className="text-sm text-text-muted" data-testid="requirements-incomplete">
              {t('incomplete')}
            </p>
          )}
          {message && (
            <p role="status" className="text-sm" data-testid="requirements-message">
              {message}
            </p>
          )}
          {duplicates.map((finding, index) => (
            <Notice
              key={`${finding.board_id ?? ''}-${String(index)}`}
              tone="critical"
              live={false}
              testId="requirements-designation-duplicate"
            >
              {t('designationDuplicate', { board: finding.board_id ?? '—' })}
            </Notice>
          ))}
          {record === null ? (
            <p className="text-sm text-text-muted" data-testid="requirements-none">
              {t('noRecord')}
            </p>
          ) : (
            <>
              {previewed !== null && (
                <p className="text-sm text-text-muted" data-testid="requirements-preview-note">
                  {t('previewNote')}
                </p>
              )}
              {record.derived && (
                <Notice tone="warning" live={false} testId="requirements-derived">
                  {t('derived')}
                </Notice>
              )}
              <h3 className="font-semibold">{t('questions')}</h3>
              {record.questions.length === 0 ? (
                <p className="text-sm text-text-muted">{t('noQuestions')}</p>
              ) : (
                <ul data-testid="requirements-questions">{record.questions.map(questionRow)}</ul>
              )}
              <h3 className="font-semibold">{t('openItems')}</h3>
              {record.open_items.length === 0 ? (
                <p className="text-sm text-text-muted">{t('noOpenItems')}</p>
              ) : (
                <ul className="flex flex-col gap-2" data-testid="requirements-items">
                  {record.open_items.map((item, index) => {
                    const subject = subjectText(item.subject);
                    return (
                      <li
                        key={`${item.code}-${String(index)}`}
                        className="flex flex-wrap items-center gap-2 text-sm"
                        data-testid={`open-item-${item.code}`}
                      >
                        <StatusBadge tone="warning">{t('itemOpen')}</StatusBadge>
                        <span>
                          {t.has(`item.${item.code}`) ? t(`item.${item.code}`) : item.code}
                        </span>
                        {subject && <bdi className="text-text-muted">{subject}</bdi>}
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
        </div>
      </Panel>

      <Panel title={t('steps.trace')} testId="requirements-trace">
        {record === null || record.trace.length === 0 ? (
          <p className="text-sm text-text-muted">{t('noTrace')}</p>
        ) : (
          <TableFrame label={t('steps.trace')}>
            <table className="data-table w-full text-sm">
              <thead>
                <tr>
                  <th className="p-2 text-start" scope="col">
                    {t('trace.requirement')}
                  </th>
                  <th className="p-2 text-start" scope="col">
                    {t('trace.design')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {record.trace.map((link) => (
                  <tr
                    key={`${link.requirement_ref}-${link.design_ref ?? ''}`}
                    data-testid={`trace-${link.requirement_ref}`}
                  >
                    <td className="p-2">
                      <bdi>{refText(link.requirement_ref)}</bdi>{' '}
                      <bdi dir="ltr" className="font-mono text-xs text-text-muted">
                        {link.requirement_ref}
                      </bdi>
                    </td>
                    <td className="p-2 font-mono" dir="ltr">
                      {link.design_ref ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableFrame>
        )}
      </Panel>
    </div>
  );
}
