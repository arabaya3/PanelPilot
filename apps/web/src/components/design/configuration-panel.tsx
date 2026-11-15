'use client';

import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import { ConfigurationEditor, entryElementId } from '@/components/design/configuration-editor';
import {
  conflictTarget,
  duplicateEntries,
  isEmptyConfiguration,
  normalized,
  removeEntry,
  entryName,
  sectionOf,
  type Configuration,
  type ConflictTarget,
} from '@/components/design/configuration-model';
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
  type ConfigurationReview,
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
}) {
  const t = useIsolatingTranslations('configuration');
  const id = useId();
  const [text, setText] = useState('');
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' });
  const [disabledHere, setDisabledHere] = useState<string | null>(null);
  const parsed = parseConfiguration(text);
  const working = outcome.kind === 'working';
  const doc = normalized(value);
  const serialized = JSON.stringify(doc);
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
    const result = await validateImpl({ token, id: projectId, configuration: doc });
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
