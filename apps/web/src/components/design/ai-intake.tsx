'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import {
  confirmedValue,
  EDIT_FIELDS,
  editDraftOf,
  isEditable,
  latestDecision,
  spanParts,
  statusOf,
  text,
  validateEdit,
  type AiStatus,
  type EditableKind,
  type EditDraft,
  type EditError,
  type PlaceProblem,
} from '@/components/design/ai-intake-model';
import { useOutcomeText } from '@/components/design/note-text';
import { NEW_LOAD_KINDS } from '@/components/design/schedule';
import { Notice, Panel, StatusBadge, type Tone } from '@/components/ui';
import {
  AI_DESCRIPTION_LIMIT,
  AI_ITEM_KINDS,
  CONTROL_NEEDS,
  decideAiItem,
  requestAiProposal,
  type AiItem,
  type AiProposal,
  type ProjectDesignRequest,
  type Refusal,
} from '@/lib/design';

const STATUS_TONE: Record<AiStatus, Tone> = {
  proposed: 'warning',
  accepted: 'success',
  edited: 'success',
  rejected: 'neutral',
};

/** The AI failures the page says in its own words, each with a Retry and a way on by hand. */
const AI_CODES = new Set([
  'ai_unavailable',
  'ai_proposal_malformed',
  'ai_timeout',
  'ai_input_too_long',
  'ai_input_empty',
  'ai_requires_account',
  'ai_proposal_used',
  'ai_proposal_not_found',
]);

type Failure = {
  message: string;
  retry: () => void;
};

type Impls = { request?: typeof requestAiProposal; decide?: typeof decideAiItem };

/**
 * M6b: the "Describe the project" step and the review of what the model
 * proposed from it. Every proposal is an unconfirmed suggestion: nothing is
 * shown as accepted until the server has recorded the decision, and nothing
 * reaches the editor except an item accepted or edited there. A failure
 * leaves the editor, the draft and the typed description as they were.
 */
export function AiIntake({
  token,
  description,
  onDescriptionChange,
  proposal,
  onProposal,
  request,
  placed,
  onPlace,
  boardNames,
  manualTargetId,
  onUnauthorized,
  impls = {},
}: {
  token: string | null;
  description: string;
  onDescriptionChange: (text: string) => void;
  proposal: AiProposal | null;
  /** A proposal as the server now holds it (a new one, or one a decision changed). */
  onProposal: (proposal: AiProposal) => void;
  /** The current draft, sent only so the model can name existing boards. */
  request: ProjectDesignRequest | null;
  /** Item ids the editor holds now. */
  placed: ReadonlySet<string>;
  /** Place a confirmed item in the editor; the reason it cannot be, else null. */
  onPlace: (item: AiItem, proposal: AiProposal) => PlaceProblem | null;
  /** Board names a load can be put on: the editor's and the proposal's. */
  boardNames: readonly string[];
  /** Where "Continue manually" moves the focus: the editor's first step. */
  manualTargetId?: string;
  onUnauthorized?: () => void;
  impls?: Impls;
}) {
  const t = useTranslations('design.ai');
  const td = useTranslations('design');
  const tr = useTranslations('design.requirements');
  const say = useOutcomeText();
  const id = useId();
  const { request: ask = requestAiProposal, decide = decideAiItem } = impls;
  const [asking, setAsking] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [itemFailure, setItemFailure] = useState<({ item: string } & Failure) | null>(null);
  const [problems, setProblems] = useState<Record<string, PlaceProblem>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft>({});
  const [editErrors, setEditErrors] = useState<Record<string, EditError>>({});
  const [shown, setShown] = useState<string | null>(null);
  const firstEditField = useRef<HTMLInputElement | HTMLSelectElement | null>(null);
  const editButtons = useRef<Record<string, HTMLButtonElement | null>>({});

  // Where the focus goes back to once an edit closes or a decision lands.
  const refocus = useRef<string | null>(null);
  useEffect(() => {
    if (editing !== null) {
      firstEditField.current?.focus();
      return;
    }
    if (refocus.current !== null && pending === null) {
      // Only where the focus was lost (a button that went disabled, a form that closed).
      const active = document.activeElement;
      if (active === null || active === document.body) {
        editButtons.current[refocus.current]?.focus();
      }
      refocus.current = null;
    }
  }, [editing, pending]);

  const length = Array.from(description).length;
  const tooLong = length > AI_DESCRIPTION_LIMIT;
  const used = proposal?.used_by_revision_id != null && proposal.used_by_revision_id !== '';

  function failureText(outcome: { kind: string }): string {
    const code = (outcome as Refusal).code ?? '';
    if (outcome.kind === 'refused' && AI_CODES.has(code)) {
      return t(`error.${code}`, { max: AI_DESCRIPTION_LIMIT });
    }
    return say(outcome);
  }

  async function requestProposals() {
    if (token === null || asking) return;
    setAsking(true);
    setFailure(null);
    const outcome = await ask({ token, description, request });
    setAsking(false);
    if (outcome.kind === 'proposed') {
      setProblems({});
      setEditing(null);
      setShown(null);
      onProposal(outcome.proposal);
      return;
    }
    if (outcome.kind === 'unauthorized') onUnauthorized?.();
    setFailure({ message: failureText(outcome), retry: () => void requestProposals() });
  }

  function continueManually() {
    setFailure(null);
    setItemFailure(null);
    if (manualTargetId) document.getElementById(manualTargetId)?.focus();
  }

  async function record(
    item: AiItem,
    decision: 'accept' | 'reject' | 'edit',
    value?: Record<string, unknown>,
  ) {
    if (token === null || proposal === null || pending !== null) return;
    setPending(item.id);
    setItemFailure(null);
    const outcome = await decide({
      token,
      proposal,
      item: item.id,
      decision,
      ...(value ? { value } : {}),
    });
    setPending(null);
    if (outcome.kind !== 'proposed') {
      if (outcome.kind === 'unauthorized') onUnauthorized?.();
      setItemFailure({
        item: item.id,
        message: failureText(outcome),
        retry: () => void record(item, decision, value),
      });
      return;
    }
    const stored = outcome.proposal;
    onProposal(stored);
    if (decision === 'edit') setEditing(null);
    // Only now, with the server's record of the decision, does the item go in.
    if (decision !== 'reject' && statusOf(stored, item.id) !== 'rejected') place(item, stored);
    else setProblems(({ [item.id]: _gone, ...rest }) => rest);
    refocus.current = item.id;
  }

  function place(item: AiItem, from: AiProposal) {
    if (!['board', 'load', 'control_need'].includes(item.kind)) return;
    const problem = onPlace(item, from);
    setProblems(({ [item.id]: _gone, ...rest }) =>
      problem ? { ...rest, [item.id]: problem } : rest,
    );
  }

  function startEdit(item: AiItem) {
    if (proposal === null || !isEditable(item.kind)) return;
    setEditing(item.id);
    setEditErrors({});
    setEditDraft(editDraftOf(item.kind, confirmedValue(proposal, item)));
  }

  function submitEdit(item: AiItem) {
    if (!isEditable(item.kind)) return;
    const checked = validateEdit(item.kind, editDraft);
    if ('errors' in checked) {
      setEditErrors(checked.errors);
      return;
    }
    setEditErrors({});
    void record(item, 'edit', checked.value);
  }

  function fieldLabel(field: string): string {
    return t(`field.${field}`);
  }

  /** A proposed field's value in the reader's language, or null where the item does not set it. */
  function valueText(item: AiItem, field: string, value: unknown): string | null {
    if (value === undefined || value === null || value === '') return null;
    if (field === 'kind' && typeof value === 'string' && td.has(`kind.${value}`)) {
      return td(`kind.${value}`);
    }
    if (field === 'need' && typeof value === 'string' && tr.has(`need.${value}`)) {
      return tr(`need.${value}`);
    }
    if (field === 'controlled' && typeof value === 'boolean') return value ? t('yes') : t('no');
    if (field === 'load' && item.kind === 'control_need' && proposal) {
      const load = proposal.items.find((entry) => entry.id === value);
      return load ? text(confirmedValue(proposal, load).description) || text(value) : text(value);
    }
    if (field === 'about' && proposal && typeof value === 'string') {
      const about = proposal.items.find((entry) => entry.id === value);
      return about ? about.quote : value;
    }
    if (field === 'reason' && typeof value === 'string' && t.has(`reason.${value}`)) {
      return t(`reason.${value}`);
    }
    return text(value) || (typeof value === 'boolean' ? String(value) : null);
  }

  function problemText(problem: PlaceProblem): string {
    switch (problem.code) {
      case 'missing':
        return t('problem.missing', { fields: problem.fields.map(fieldLabel).join(', ') });
      case 'boardNotInEditor':
        return t('problem.boardNotInEditor', { board: problem.board });
      case 'boardExists':
        return t('problem.boardExists', { board: problem.board });
      case 'loadNotPlaced':
        return t('problem.loadNotPlaced');
      default:
        return t('problem.notPlaceable');
    }
  }

  function editForm(item: AiItem & { kind: EditableKind }) {
    const fields = EDIT_FIELDS[item.kind];
    const fieldId = (field: string) => `${id}-edit-${item.id}-${field}`;
    const error = (field: string) => {
      const code = editErrors[field];
      return code ? t(`editError.${code}`) : undefined;
    };
    const set = (field: string, value: string) => {
      setEditDraft((current) => ({ ...current, [field]: value }));
    };
    return (
      <form
        className="grid grid-cols-1 gap-3 rounded-md border border-border p-3 sm:grid-cols-2"
        data-testid={`ai-edit-${item.id}`}
        aria-label={t('editOf', { quote: item.quote })}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          submitEdit(item);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setEditing(null);
            refocus.current = item.id;
          }
        }}
      >
        {fields.map((field, index) => {
          const ref = index === 0 ? { ref: firstEditField as never } : {};
          const common = {
            id: fieldId(field),
            value: editDraft[field] ?? '',
            'aria-invalid': editErrors[field] ? true : undefined,
            'data-testid': `ai-edit-${item.id}-${field}`,
            className: 'input w-full',
            ...ref,
          };
          let control;
          if (field === 'kind') {
            control = (
              <select
                {...common}
                onChange={(event) => {
                  set(field, event.target.value);
                }}
              >
                <option value="">{t('choose')}</option>
                {NEW_LOAD_KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {td.has(`kind.${kind}`) ? td(`kind.${kind}`) : kind}
                  </option>
                ))}
              </select>
            );
          } else if (field === 'need') {
            control = (
              <select
                {...common}
                onChange={(event) => {
                  set(field, event.target.value);
                }}
              >
                <option value="">{t('choose')}</option>
                {CONTROL_NEEDS.map((need) => (
                  <option key={need} value={need}>
                    {tr(`need.${need}`)}
                  </option>
                ))}
              </select>
            );
          } else if (field === 'phases') {
            control = (
              <select
                {...common}
                onChange={(event) => {
                  set(field, event.target.value);
                }}
              >
                <option value="">{t('notStated')}</option>
                <option value="1">1</option>
                <option value="3">3</option>
              </select>
            );
          } else if (field === 'controlled') {
            control = (
              <select
                {...common}
                onChange={(event) => {
                  set(field, event.target.value);
                }}
              >
                <option value="">{t('notStated')}</option>
                <option value="true">{t('yes')}</option>
                <option value="false">{t('no')}</option>
              </select>
            );
          } else if (field === 'board' || field === 'fed_from') {
            const own = item.kind === 'board' ? (editDraft.name ?? '').trim() : '';
            const names = boardNames.filter((name) => name !== own);
            const current = editDraft[field] ?? '';
            control = (
              <select
                {...common}
                dir="ltr"
                onChange={(event) => {
                  set(field, event.target.value);
                }}
              >
                <option value="">{field === 'fed_from' ? td('fedFromNone') : t('choose')}</option>
                {[...new Set(current !== '' ? [current, ...names] : names)].map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            );
          } else {
            const numeric = ['power_kw', 'power_factor', 'length_m'].includes(field);
            control = (
              <input
                {...common}
                {...(numeric ? { inputMode: 'decimal' as const, dir: 'ltr' } : { dir: 'auto' })}
                onChange={(event) => {
                  set(field, event.target.value);
                }}
              />
            );
          }
          return (
            <Field key={field} id={fieldId(field)} label={fieldLabel(field)} error={error(field)}>
              {control}
            </Field>
          );
        })}
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <button
            type="submit"
            className="btn btn-sm btn-primary"
            disabled={pending !== null}
            data-testid={`ai-edit-save-${item.id}`}
          >
            {t('saveEdit')}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => {
              setEditing(null);
              refocus.current = item.id;
            }}
          >
            {t('cancelEdit')}
          </button>
        </div>
      </form>
    );
  }

  function itemRow(item: AiItem) {
    if (proposal === null) return null;
    const status = statusOf(proposal, item.id);
    const decision = latestDecision(proposal, item.id);
    const value = confirmedValue(proposal, item);
    const isPlaced = placed.has(item.id);
    const placeable = ['board', 'load', 'control_need'].includes(item.kind);
    const confirmed = status === 'accepted' || status === 'edited';
    const busy = pending === item.id;
    // A placed item is the editor's now: remove its row first to change the decision.
    const locked = used || isPlaced || pending !== null || token === null;
    const problem = problems[item.id];
    const failed = itemFailure?.item === item.id ? itemFailure : null;
    const fields = Object.entries(value).filter(
      ([, entry]) => entry !== undefined && entry !== null,
    );
    const statusId = `${id}-status-${item.id}`;
    return (
      <li
        key={item.id}
        className="flex min-w-0 flex-col gap-2 border-b border-border py-3 last:border-b-0"
        data-testid={`ai-item-${item.id}`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span id={statusId}>
            <StatusBadge tone={STATUS_TONE[status]} testId={`ai-status-${item.id}`}>
              {busy ? t('recording') : t(`status.${status}`)}
            </StatusBadge>
          </span>
          {isPlaced && (
            <StatusBadge tone="info" testId={`ai-placed-${item.id}`}>
              {t('inEditor')}
            </StatusBadge>
          )}
        </div>
        <blockquote className="min-w-0 break-words border-s-2 border-border ps-3 text-sm">
          <bdi dir="auto" data-testid={`ai-quote-${item.id}`}>
            {item.quote}
          </bdi>
        </blockquote>
        <button
          type="button"
          className="btn btn-sm btn-ghost self-start"
          aria-pressed={shown === item.id}
          data-testid={`ai-show-${item.id}`}
          onClick={() => {
            setShown((current) => (current === item.id ? null : item.id));
          }}
        >
          {t('showInText')}
        </button>
        {fields.length > 0 && (
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
            {fields.map(([field, entry]) => {
              const said = valueText(item, field, entry);
              if (said === null) return null;
              return (
                <div key={field} className="contents">
                  <dt className="text-text-muted">
                    {t.has(`field.${field}`) ? fieldLabel(field) : field}
                  </dt>
                  <dd className="min-w-0 break-words" data-testid={`ai-value-${item.id}-${field}`}>
                    <bdi dir="auto">{said}</bdi>
                  </dd>
                </div>
              );
            })}
          </dl>
        )}
        {decision !== null && (
          <p className="text-xs text-text-muted" data-testid={`ai-decided-${item.id}`}>
            {t(`decidedBy.${decision.decision}`, {
              name: decision.by_name || t('someone'),
              date: decision.at.slice(0, 16).replace('T', ' '),
            })}
          </p>
        )}
        {problem && (
          <p className="text-sm text-severity-critical" data-testid={`ai-problem-${item.id}`}>
            {problemText(problem)}
          </p>
        )}
        {editing === item.id && isEditable(item.kind) ? (
          editForm(item as AiItem & { kind: EditableKind })
        ) : (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              disabled={locked || status === 'accepted'}
              aria-describedby={statusId}
              data-testid={`ai-accept-${item.id}`}
              onClick={() => void record(item, 'accept')}
            >
              {t('accept')}
            </button>
            {isEditable(item.kind) && (
              <button
                type="button"
                className="btn btn-sm btn-secondary"
                disabled={locked}
                aria-describedby={statusId}
                ref={(element) => {
                  editButtons.current[item.id] = element;
                }}
                data-testid={`ai-edit-open-${item.id}`}
                onClick={() => {
                  startEdit(item);
                }}
              >
                {t('edit')}
              </button>
            )}
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              disabled={locked || status === 'rejected'}
              aria-describedby={statusId}
              data-testid={`ai-reject-${item.id}`}
              onClick={() => void record(item, 'reject')}
            >
              {t('reject')}
            </button>
            {placeable && confirmed && !isPlaced && !used && (
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                data-testid={`ai-place-${item.id}`}
                onClick={() => {
                  place(item, proposal);
                }}
              >
                {t('place')}
              </button>
            )}
          </div>
        )}
        {isPlaced && !used && <p className="text-xs text-text-muted">{t('placedLocked')}</p>}
        {failed && (
          <div
            className="flex flex-col gap-2"
            role="alert"
            data-testid={`ai-item-error-${item.id}`}
          >
            <p className="text-sm text-severity-critical">{failed.message}</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-sm btn-secondary" onClick={failed.retry}>
                {t('retry')}
              </button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={continueManually}>
                {t('continueManually')}
              </button>
            </div>
          </div>
        )}
      </li>
    );
  }

  const highlighted = proposal?.items.find((item) => item.id === shown) ?? null;
  const parts =
    proposal && highlighted
      ? spanParts(proposal.input_text, highlighted.source.start, highlighted.source.end)
      : null;
  const counterId = `${id}-count`;
  const noticeId = `${id}-notice`;

  return (
    <div className="flex flex-col gap-5" data-testid="ai-intake">
      <Panel title={t('title')} testId="ai-describe">
        <div className="flex flex-col gap-3">
          <div id={noticeId}>
            <Notice tone="info" live={false} testId="ai-notice">
              {t('notice')}
            </Notice>
          </div>
          <label htmlFor={`${id}-description`} className="field-label">
            {t('label')}
          </label>
          <textarea
            id={`${id}-description`}
            dir="auto"
            rows={6}
            value={description}
            aria-describedby={`${counterId} ${noticeId}`}
            aria-invalid={tooLong || undefined}
            data-testid="ai-description"
            className="input w-full"
            onChange={(event) => {
              onDescriptionChange(event.target.value);
            }}
          />
          <p
            id={counterId}
            className={`text-xs ${tooLong ? 'text-severity-critical' : 'text-text-muted'}`}
            data-testid="ai-count"
          >
            <bdi dir="ltr">{t('count', { count: length, max: AI_DESCRIPTION_LIMIT })}</bdi>
            {tooLong ? ` ${t('error.ai_input_too_long', { max: AI_DESCRIPTION_LIMIT })}` : ''}
          </p>
          <button
            type="button"
            className="btn btn-primary self-start"
            disabled={token === null || asking || tooLong || description.trim() === ''}
            data-testid="ai-request"
            onClick={() => void requestProposals()}
          >
            {asking ? t('requesting') : proposal ? t('requestAgain') : t('request')}
          </button>
          {proposal && !used && <p className="text-xs text-text-muted">{t('requestAgainNote')}</p>}
          {failure && (
            <div className="flex flex-col gap-2" role="alert" data-testid="ai-error">
              <p className="text-sm text-severity-critical">{failure.message}</p>
              <p className="text-xs text-text-muted">{t('nothingChanged')}</p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  data-testid="ai-retry"
                  onClick={failure.retry}
                >
                  {t('retry')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  data-testid="ai-manual"
                  onClick={continueManually}
                >
                  {t('continueManually')}
                </button>
              </div>
            </div>
          )}
        </div>
      </Panel>

      {proposal && (
        <Panel title={t('reviewTitle')} testId="ai-review">
          <div className="flex flex-col gap-4">
            <Notice tone="warning" live={false} testId="ai-unconfirmed">
              {t('unconfirmed')}
            </Notice>
            {used && (
              <Notice tone="info" live={false} testId="ai-used">
                {t('error.ai_proposal_used')}
              </Notice>
            )}
            <p className="text-xs text-text-muted" data-testid="ai-provider">
              {t('provenance', { provider: proposal.provider, model: proposal.model })}
            </p>
            <div>
              <h3 className="mb-1 text-sm font-semibold">{t('original')}</h3>
              <p
                dir="auto"
                className="min-w-0 whitespace-pre-wrap break-words rounded-md border border-border p-3 text-sm"
                data-testid="ai-original"
              >
                {parts ? (
                  <>
                    {parts.before}
                    <mark data-testid="ai-highlight">{parts.span}</mark>
                    {parts.after}
                  </>
                ) : (
                  proposal.input_text
                )}
              </p>
            </div>
            {AI_ITEM_KINDS.map((kind) => {
              const items = proposal.items.filter((item) => item.kind === kind);
              if (items.length === 0) return null;
              return (
                <section key={kind} data-testid={`ai-group-${kind}`}>
                  <h3 className="font-semibold">{t(`kind.${kind}`, { count: items.length })}</h3>
                  {kind === 'board' && (
                    <p className="text-xs text-text-muted">{t('boardSupplyNote')}</p>
                  )}
                  <ul>{items.map(itemRow)}</ul>
                </section>
              );
            })}
            {proposal.server_rejected.length > 0 && (
              <section data-testid="ai-group-rejected">
                <h3 className="font-semibold">
                  {t('kind.server_rejected', { count: proposal.server_rejected.length })}
                </h3>
                <p className="text-xs text-text-muted">{t('serverRejectedNote')}</p>
                <ul>
                  {proposal.server_rejected.map((entry, index) => (
                    <li
                      key={index}
                      className="flex min-w-0 flex-col gap-1 border-b border-border py-3 text-sm last:border-b-0"
                      data-testid={`ai-rejected-${String(index)}`}
                    >
                      <StatusBadge tone="critical">{t('status.server_rejected')}</StatusBadge>
                      {entry.quote && (
                        <blockquote className="min-w-0 break-words border-s-2 border-border ps-3">
                          <bdi dir="auto">{entry.quote}</bdi>
                        </blockquote>
                      )}
                      <p>
                        {t('rejectedReason')}{' '}
                        <bdi dir="auto" data-testid={`ai-rejected-reason-${String(index)}`}>
                          {t.has(`serverReason.${entry.reason}`)
                            ? t(`serverReason.${entry.reason}`)
                            : entry.reason}
                        </bdi>
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </Panel>
      )}
    </div>
  );
}
