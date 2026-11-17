'use client';

import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import { ConfigurationEditor, entryElementId } from '@/components/design/configuration-editor';
import {
  conflictTarget,
  duplicateEntries,
  emptyConfiguration,
  isEmptyConfiguration,
  normalized,
  removeEntry,
  entryName,
  sectionOf,
  type Configuration,
  type ConflictTarget,
} from '@/components/design/configuration-model';
import {
  choicesOf,
  movedOf,
  type BoardChoices,
  type MovedReference,
} from '@/components/design/binding-model';
import type { ConfirmedReference } from '@/components/design/draft';
import type { LinkEndUse } from '@/components/design/definition-model';
import {
  GroupedFindingsOfDesign,
  ModelReview,
  ValidationScopeNote,
} from '@/components/design/configuration-review';
import { FilePicker } from '@/components/file-picker';
import { TechnicalToken } from '@/components/technical-token';
import { LoadingState, Notice } from '@/components/ui';
import {
  validateConfiguration,
  type ConfigurationOutcome,
  type ConfigurationReview,
  type ProjectDesignRequest,
  type ConfigurationStatus,
  type DesignFinding,
  type Refusal,
  type V2ModelSummary,
} from '@/lib/design';

/** The refusals a configuration is answered with that have their own sentence. */
const CONFIGURATION_REFUSALS = [
  'configuration_requires_v2',
  'configuration_claims_review',
  'configuration_ref_reserved',
  'configuration_refused',
  'configuration_too_large',
  'configuration_not_recoverable',
] as const;

/** Whether a refusal is about the configuration (shown by this panel). */
export function isConfigurationRefusal(refusal: Refusal): boolean {
  const code = refusal.code ?? '';
  return (
    code === 'configuration_conflict' ||
    (CONFIGURATION_REFUSALS as readonly string[]).includes(code)
  );
}

type ErrorOutcome = {
  kind: 'error';
  message: string;
  disabled: string | null;
  conflict: ConflictTarget | null;
};

type Outcome =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'reviewed'; review: ConfigurationReview; text: string }
  | { kind: 'saved'; revision: number }
  | ErrorOutcome;

/** The configuration a text holds: an object, or why it is not one. */
export function parseConfiguration(text: string): Record<string, unknown> | 'empty' | 'invalid' {
  if (text.trim() === '') return 'empty';
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === 'object' && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : 'invalid';
  } catch {
    return 'invalid';
  }
}

/**
 * What one Save sends with the form's request (M7b §3): the configuration as
 * shown (always sent, so a save from an older revision stores that revision's
 * own, never the latest one); its explicit removal when the editor was
 * emptied of a stored one, or the user chose to save without one that cannot
 * be carried; otherwise nothing (there is none).
 */
export function configurationPayload(
  value: Configuration,
  status: ConfigurationStatus,
  dropUnrecoverable = false,
): { configuration?: Configuration; configurationRemoved?: true } {
  if (!isEmptyConfiguration(value)) return { configuration: value };
  if (status === 'stored') return { configurationRemoved: true };
  return status === 'not_recoverable' && dropUnrecoverable ? { configurationRemoved: true } : {};
}

/**
 * The guided configuration of the opened project: edited in a form (JSON
 * import is an advanced option) and stored with the form's request by one
 * Save. A refused save keeps everything entered; a conflict names the
 * reference and offers to edit or remove what holds it.
 */
export function ConfigurationPanel({
  token,
  projectId,
  latestRevision,
  value,
  onChange,
  status,
  dropUnrecoverable = false,
  onDropUnrecoverable,
  boards,
  model,
  externalRefusal = null,
  saveAction = null,
  onUnauthorized,
  validateImpl = validateConfiguration,
  findings,
  findingsRevision,
  linkUses = [],
  boardChoices = null,
  currentRequest = null,
  confirmed = [],
  onConfirm,
  jumpToConflict = 0,
}: {
  token: string;
  projectId: string;
  latestRevision: number;
  /** The configuration as edited (the page keeps it, so it outlives this panel). */
  value: Configuration;
  onChange: (next: Configuration) => void;
  /** The opened revision's `configuration_status`. */
  status: ConfigurationStatus;
  /** The user chose to save without a configuration that cannot be carried. */
  dropUnrecoverable?: boolean;
  onDropUnrecoverable?: (drop: boolean) => void;
  /** The form's board names. */
  boards: string[];
  model: V2ModelSummary | null;
  /** A configuration refusal the project's own Save was answered with. */
  externalRefusal?: Refusal | null;
  /** The way to the project's Save, which stores the configuration with the form. */
  saveAction?: ReactNode;
  onUnauthorized: () => void;
  validateImpl?: typeof validateConfiguration;
  /** The opened revision's design findings (only `designation_duplicate` is shown here). */
  findings?: DesignFinding[];
  /** The revision those findings were found in. */
  findingsRevision?: number;
  /** The feeder-link ends the boards' link forms name now (M8). */
  linkUses?: LinkEndUse[];
  /** Each board's circuits and cables from the opened design (M9b1); null when not known. */
  boardChoices?: BoardChoices[] | null;
  /** Bumped by the overview's "show it" action: the refused entry is focused (M9b1). */
  jumpToConflict?: number;
  /**
   * The load schedule as the editor holds it now, in the shape Save sends: the preview and
   * Validate design and compose it with the configuration, as Save does. Null when the
   * form cannot be sent (incomplete): then the latest saved revision's is used, as before.
   */
  currentRequest?: ProjectDesignRequest | null;
  /** References the user keeps for the load now at their position (sent, never stored). */
  confirmed?: ConfirmedReference[];
  onConfirm?: ((entry: ConfirmedReference) => void) | undefined;
}) {
  const t = useIsolatingTranslations('configuration');
  const id = useId();
  const [text, setText] = useState('');
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' });
  const [disabledHere, setDisabledHere] = useState<string | null>(null);
  const parsed = parseConfiguration(text);
  const working = outcome.kind === 'working';
  const doc = normalized(value);
  // What a preview or validation answers: the configuration and the load schedule (and the
  // confirmations) as they are now; any change makes an earlier answer outdated.
  const inputsKey = JSON.stringify({ request: currentRequest, confirmed });
  const serialized = `${JSON.stringify(doc)}|${inputsKey}`;
  const sent = {
    ...(currentRequest !== null ? { request: currentRequest } : {}),
    ...(confirmed.length > 0 ? { referencesConfirmed: confirmed } : {}),
  };
  // M10: circuit and cable choices from the server's design of the current inputs.
  const [previewChoices, setPreviewChoices] = useState<{
    key: string;
    boards: BoardChoices[];
  } | null>(null);
  // References the server found moved (from a preview, a validation or a save refusal).
  const [moved, setMoved] = useState<{ key: string; refs: MovedReference[] } | null>(null);
  const requestKey = JSON.stringify(currentRequest);
  const currentKey = useRef(serialized);
  currentKey.current = serialized;
  const choices: BoardChoices[] | null =
    boardChoices ??
    (previewChoices !== null && previewChoices.key === requestKey ? previewChoices.boards : null);
  const movedNow = moved !== null && moved.key === serialized ? moved.refs : [];

  /** What a preview or validation answer says for the choices and moved references. */
  function took(result: ConfigurationOutcome, forKey: string, forRequest: string) {
    if (result.kind === 'reviewed') {
      if (result.review.from_current_request) {
        setPreviewChoices({ key: forRequest, boards: choicesOf(result.review.boards ?? []) });
      }
      // Only an answer for the configuration as it is now clears what was found moved.
      if (forKey === currentKey.current) setMoved(null);
    } else if (result.kind === 'refused' && result.params?.refusal === 'reference_moved') {
      setMoved({ key: forKey, refs: movedOf(result.params) });
    }
  }

  async function refreshChoices() {
    if (currentRequest === null) return;
    const forRequest = requestKey;
    // An empty configuration composes the design alone: choices do not wait on the bindings.
    const result = await validateImpl({
      token,
      id: projectId,
      configuration: normalized(emptyConfiguration()),
      request: currentRequest,
    });
    if (result.kind === 'reviewed' && result.review.from_current_request) {
      setPreviewChoices({ key: forRequest, boards: choicesOf(result.review.boards ?? []) });
    } else if (result.kind === 'unauthorized') {
      onUnauthorized();
    }
  }
  const focusResult = useRef(false);
  useEffect(() => {
    if (!focusResult.current || outcome.kind === 'working' || outcome.kind === 'idle') return;
    focusResult.current = false;
    document.getElementById(`${id}-result`)?.focus();
  }, [outcome, id]);

  function refusalText(refusal: Refusal): ErrorOutcome {
    const code = refusal.code ?? '';
    const params = refusal.params ?? {};
    if (code === 'configuration_conflict') {
      const conflict = conflictTarget(params);
      return {
        kind: 'error',
        message: t('refusal.configuration_conflict', {
          ref: params.ref ?? '',
          refKind: params.kind ?? '',
          entry: conflict
            ? t(`editor.entryKind.${conflict.kind}`, {
                id: entryName(doc, conflict.kind, conflict.entry),
              })
            : '',
          reason: params.reason ?? '',
        }),
        disabled: null,
        conflict,
      };
    }
    const named = code === 'configuration_refused' ? conflictTarget(params) : null;
    if (named !== null && named.kind === 'pattern') {
      // M9a: refused for a pattern itself: said with the net, slot and terminal the server names.
      const dash = (value: string | undefined) =>
        value === undefined || value === '' ? '—' : value;
      return {
        kind: 'error',
        message: t('refusal.configuration_refused_pattern', {
          pattern: entryName(doc, 'pattern', named.entry),
          refusal: named.reason,
          net: dash(params.role),
          slot: dash(params.slot),
          terminal: dash(params.terminal),
        }),
        disabled: null,
        conflict: named,
      };
    }
    if (named !== null) {
      // Refused for one entry (M7b): said and marked like a conflict, with its board.
      const board =
        named.kind === 'strip' || named.kind === 'component' || named.kind === 'binding'
          ? ((doc[sectionOf(named.kind) ?? 'strips'] as { id?: unknown; board?: unknown }[]).find(
              (item) => item.id === named.entry,
            )?.board ?? '')
          : '';
      return {
        kind: 'error',
        message: t('refusal.configuration_refused_entry', {
          entry: t(`editor.entryKind.${named.kind}`, {
            id: entryName(doc, named.kind, named.entry),
          }),
          board: typeof board === 'string' && board !== '' ? board : '—',
          ref: named.ref === '' ? '—' : named.ref,
          refusal: named.reason,
        }),
        disabled: null,
        conflict: named,
      };
    }
    if ((CONFIGURATION_REFUSALS as readonly string[]).includes(code)) {
      return {
        kind: 'error',
        message: t(`refusal.${code}`, {
          schema: params.written_schema ?? 'pp-design/1',
          ref: params.ref ?? '',
          fields: params.fields ?? '',
          refusal: params.refusal ?? '',
          limitOf: params.limit_of ?? '',
          limit: params.limit ?? '',
          revision: params.revision ?? String(latestRevision),
        }),
        disabled:
          code === 'configuration_requires_v2' ? (params.written_schema ?? 'pp-design/1') : null,
        conflict: null,
      };
    }
    if (code === 'revision_not_validated') {
      return {
        kind: 'error',
        message: t('refusal.revision_not_validated'),
        disabled: null,
        conflict: null,
      };
    }
    return {
      kind: 'error',
      message: refusal.detail ? t('refusal.other', { detail: refusal.detail }) : t('failed'),
      disabled: null,
      conflict: null,
    };
  }

  // A configuration refusal of the project's own Save is shown here too.
  const [seenExternal, setSeenExternal] = useState<Refusal | null>(null);
  if (externalRefusal !== seenExternal) {
    setSeenExternal(externalRefusal);
    if (externalRefusal !== null) setOutcome(refusalText(externalRefusal));
    // A save refused for a moved reference: say it beside the binding, as a preview does.
    if (externalRefusal?.params?.refusal === 'reference_moved') {
      setMoved({ key: serialized, refs: movedOf(externalRefusal.params) });
    }
  }

  function failed(result: { kind: 'unauthorized' } | { kind: 'failed' } | Refusal) {
    if (result.kind === 'unauthorized') {
      onUnauthorized();
      setOutcome({ kind: 'error', message: t('unauthorized'), disabled: null, conflict: null });
      return;
    }
    if (result.kind === 'failed') {
      setOutcome({ kind: 'error', message: t('failed'), disabled: null, conflict: null });
      return;
    }
    const said = refusalText(result);
    if (said.disabled !== null) setDisabledHere(said.disabled);
    setOutcome(said);
  }

  async function validate() {
    if (working || isEmptyConfiguration(doc)) return;
    focusResult.current = true;
    setOutcome({ kind: 'working' });
    const forKey = serialized;
    const result = await validateImpl({ token, id: projectId, configuration: doc, ...sent });
    took(result, forKey, requestKey);
    if (result.kind === 'reviewed') {
      setDisabledHere(result.review.saveable ? null : result.review.written_schema);
      setOutcome({ kind: 'reviewed', review: result.review, text: serialized });
    } else {
      failed(result);
    }
  }

  function editConflict(conflict: ConflictTarget) {
    const element = document.getElementById(entryElementId(id, conflict.kind, conflict.entry));
    // Not every browser environment scrolls (a test's DOM has no scrollIntoView).
    if (element !== null && 'scrollIntoView' in element)
      element.scrollIntoView({ block: 'center' });
    element?.focus();
  }

  const conflictNow = outcome.kind === 'error' ? outcome.conflict : null;
  useEffect(() => {
    if (jumpToConflict === 0 || conflictNow === null) return;
    const timer = window.setTimeout(() => {
      editConflict(conflictNow);
    }, 0);
    return () => {
      window.clearTimeout(timer);
    };
    // Only a new request to jump moves focus, not every render.
  }, [jumpToConflict]);

  const reviewed = outcome.kind === 'reviewed' ? outcome : null;
  const reviewCurrent = reviewed !== null && reviewed.text === serialized;
  const conflict = outcome.kind === 'error' ? outcome.conflict : null;
  const conflictSection = conflict ? sectionOf(conflict.kind) : null;
  const duplicates = (findings ?? []).filter((finding) => finding.code === 'designation_duplicate');

  return (
    <div className="flex flex-col gap-4" data-testid="configuration-panel">
      <p className="text-sm text-text-muted">{t('intro', { revision: String(latestRevision) })}</p>
      {status === 'not_recoverable' && (
        <Notice tone="warning" live={false} testId="configuration-not-recoverable">
          {t('notRecoverable', { revision: String(latestRevision) })}
          {onDropUnrecoverable && isEmptyConfiguration(doc) && (
            <label className="mt-2 flex items-start gap-2">
              <input
                type="checkbox"
                checked={dropUnrecoverable}
                onChange={(event) => {
                  onDropUnrecoverable(event.target.checked);
                }}
                data-testid="configuration-drop-unrecoverable"
              />
              <span>{t('dropUnrecoverable')}</span>
            </label>
          )}
        </Notice>
      )}
      {duplicates.length > 0 && (
        <Notice tone="critical" live={false} testId="configuration-duplicates">
          <span className="flex flex-col gap-2">
            <span className="font-semibold">{t('duplicates.title')}</span>
            {findingsRevision !== undefined && (
              <span>{t('duplicates.asSaved', { revision: String(findingsRevision) })}</span>
            )}
            <ul className="flex list-none flex-col gap-2 p-0">
              {duplicates.map((finding, index) => {
                const values = finding.values ?? {};
                const board = values.board_id ?? finding.board_id ?? '';
                const forms = values.designation ?? '';
                const parts = (values.parts ?? '').split(',').filter((part) => part !== '');
                const entries = duplicateEntries(doc, board, forms);
                return (
                  <li
                    key={`${board}|${forms}|${String(index)}`}
                    className="flex flex-col gap-1 break-words"
                    data-testid="configuration-duplicate"
                  >
                    <span>
                      {t('duplicates.item', {
                        designation: forms,
                        board,
                        count: String(parts.length),
                      })}
                    </span>
                    {entries.length > 0 && (
                      <span className="flex flex-wrap gap-2">
                        {entries.map((entry) => (
                          <button
                            key={`${entry.kind}|${entry.id}`}
                            type="button"
                            className="btn btn-sm btn-secondary"
                            onClick={() => {
                              editConflict({
                                kind: entry.kind,
                                entry: entry.id,
                                ref: '',
                                refKind: '',
                                reason: '',
                              });
                            }}
                            data-testid="duplicate-edit"
                          >
                            {t('duplicates.edit', {
                              entry: t(`editor.entryKind.${entry.kind}`, {
                                id: entryName(doc, entry.kind, entry.id),
                              }),
                            })}
                          </button>
                        ))}
                      </span>
                    )}
                    {entries.length < parts.length && (
                      <span className="text-sm">
                        {entries.length === 0 ? t('duplicates.none') : t('duplicates.derived')}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </span>
        </Notice>
      )}
      <ConfigurationEditor
        value={doc}
        onChange={onChange}
        boards={boards}
        model={model}
        conflict={conflict}
        idBase={id}
        linkUses={linkUses}
        boardChoices={choices}
        onUnauthorized={onUnauthorized}
        savedRevision={latestRevision}
        validation={
          reviewed === null
            ? null
            : {
                findings: reviewed.review.model.findings,
                current: reviewCurrent,
                revision: reviewed.review.based_on_revision,
              }
        }
        preview={async (configuration) => {
          const forKey = `${JSON.stringify(normalized(configuration))}|${inputsKey}`;
          const forRequest = requestKey;
          const result = await validateImpl({
            token,
            id: projectId,
            configuration: normalized(configuration),
            ...sent,
          });
          took(result, forKey, forRequest);
          return result;
        }}
        previewInputsKey={inputsKey}
        previewCurrent={currentRequest !== null}
        moved={movedNow}
        onConfirm={onConfirm}
        onRefreshChoices={
          currentRequest !== null && choices === null
            ? () => {
                void refreshChoices();
              }
            : undefined
        }
      />
      <Notice tone="info" live={false} action={saveAction} testId="configuration-save-where">
        {t('saveWhere')}
      </Notice>
      {disabledHere !== null && (
        <Notice tone="warning" live={false} testId="configuration-disabled">
          {t('refusal.configuration_requires_v2', { schema: disabledHere })}
        </Notice>
      )}
      {working && <LoadingState>{t('working')}</LoadingState>}
      <div
        id={`${id}-result`}
        tabIndex={-1}
        className="flex flex-col gap-4"
        data-testid="configuration-result"
      >
        {outcome.kind === 'error' && outcome.disabled === null && (
          <Notice tone="critical" testId="configuration-error">
            <span className="flex flex-col gap-2">
              <span className="break-words">{outcome.message}</span>
              {conflict && (
                <span className="flex flex-wrap gap-2" data-testid="configuration-conflict">
                  {conflictSection !== null ? (
                    <>
                      <button
                        type="button"
                        className="btn btn-sm btn-secondary"
                        onClick={() => {
                          editConflict(conflict);
                        }}
                        data-testid="conflict-edit"
                      >
                        {t('conflict.edit')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        onClick={() => {
                          onChange(removeEntry(doc, conflictSection, conflict.entry));
                          setOutcome({ kind: 'idle' });
                        }}
                        data-testid="conflict-remove"
                      >
                        {t('conflict.remove', {
                          entry: t(`editor.entryKind.${conflict.kind}`, {
                            id: entryName(doc, conflict.kind, conflict.entry),
                          }),
                        })}
                      </button>
                    </>
                  ) : conflict.kind === 'template' || conflict.kind === 'pattern' ? (
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      onClick={() => {
                        editConflict(conflict);
                      }}
                      data-testid="conflict-edit"
                    >
                      {t(
                        conflict.kind === 'pattern'
                          ? 'conflict.editPattern'
                          : 'conflict.editDefinition',
                      )}
                    </button>
                  ) : (
                    <span data-testid="conflict-link">{t('conflict.link')}</span>
                  )}
                </span>
              )}
            </span>
          </Notice>
        )}
        {outcome.kind === 'saved' && (
          <Notice tone="success" testId="configuration-saved">
            {t('saved', { revision: String(outcome.revision) })}
          </Notice>
        )}
        {reviewed && (
          <div className="flex flex-col gap-4" data-testid="configuration-review">
            <ValidationScopeNote />
            {!reviewCurrent && (
              <Notice tone="warning" live={false}>
                {t('reviewOutdated')}
              </Notice>
            )}
            <p className="text-sm">
              {t('reviewedAgainst', { revision: String(reviewed.review.based_on_revision) })}{' '}
              <TechnicalToken>{reviewed.review.snapshot_schema}</TechnicalToken>{' '}
              <TechnicalToken className="text-xs">{reviewed.review.snapshot_sha256}</TechnicalToken>
            </p>
            <ModelReview model={reviewed.review.model} context={t('contextValidation')} />
            <GroupedFindingsOfDesign findings={reviewed.review.findings} />
          </div>
        )}
      </div>

      <details
        className="rounded-md border border-border-subtle p-3"
        data-testid="configuration-advanced"
      >
        <summary className="cursor-pointer text-sm font-semibold">{t('advanced')}</summary>
        <div className="mt-3 flex flex-col gap-4">
          <FilePicker
            id={`${id}-file`}
            label={t('file')}
            help={t('fileHelp')}
            accept=".json,application/json"
            disabled={working}
            onFile={(file) => {
              void file.text().then(setText);
            }}
          />
          <div className="flex flex-col gap-1">
            <label htmlFor={`${id}-json`} className="text-sm font-semibold">
              {t('json')}
            </label>
            <textarea
              id={`${id}-json`}
              dir="ltr"
              rows={8}
              value={text}
              spellCheck={false}
              aria-invalid={parsed === 'invalid' || undefined}
              aria-describedby={parsed === 'invalid' ? `${id}-invalid` : undefined}
              onChange={(event) => {
                setText(event.target.value);
              }}
              className="input w-full font-mono text-sm"
              data-testid="configuration-json"
            />
            {parsed === 'invalid' && (
              <p id={`${id}-invalid`} className="text-sm text-text-muted">
                {t('invalid')}
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              disabled={typeof parsed !== 'object' || working}
              onClick={() => {
                if (typeof parsed === 'object') onChange(normalized(parsed));
              }}
              data-testid="configuration-import"
            >
              {t('import')}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              disabled={working || isEmptyConfiguration(doc)}
              onClick={() => {
                void validate();
              }}
              data-testid="configuration-validate"
            >
              {t('validate')}
            </button>
          </div>
          <p className="text-sm text-text-muted">{t('validateHelp')}</p>
        </div>
      </details>
    </div>
  );
}
