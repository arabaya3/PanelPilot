'use client';

import { useEffect, useId, useRef, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import { ConfigurationEditor, entryElementId } from '@/components/design/configuration-editor';
import {
  conflictTarget,
  isEmptyConfiguration,
  normalized,
  removeEntry,
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
  reviseProject,
  validateConfiguration,
  type ConfigurationReview,
  type ConfigurationStatus,
  type ProjectDesignRequest,
  type Refusal,
  type SavedProject,
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
  return code === 'configuration_conflict' || code === 'configuration_not_recoverable';
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
 * edited; its explicit removal when the editor was emptied of a stored one;
 * or nothing, so the server carries none, when there never was one.
 */
export function configurationPayload(
  value: Configuration,
  status: ConfigurationStatus,
): { configuration?: Configuration; configurationRemoved?: true } {
  if (!isEmptyConfiguration(value)) return { configuration: value };
  return status === 'stored' ? { configurationRemoved: true } : {};
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
  request,
  note,
  value,
  onChange,
  status,
  boards,
  model,
  externalRefusal = null,
  onSaved,
  onUnauthorized,
  validateImpl = validateConfiguration,
  reviseImpl = reviseProject,
}: {
  token: string;
  projectId: string;
  latestRevision: number;
  /** What a save sends as the request: the form as it stands, or null while incomplete. */
  request: ProjectDesignRequest | null;
  note: string;
  /** The configuration as edited (the page keeps it, so it outlives this panel). */
  value: Configuration;
  onChange: (next: Configuration) => void;
  /** The opened revision's `configuration_status`. */
  status: ConfigurationStatus;
  /** The form's board names. */
  boards: string[];
  model: V2ModelSummary | null;
  /** A configuration refusal the project's own Save was answered with. */
  externalRefusal?: Refusal | null;
  onSaved: (project: SavedProject) => void;
  onUnauthorized: () => void;
  validateImpl?: typeof validateConfiguration;
  reviseImpl?: typeof reviseProject;
}) {
  const t = useIsolatingTranslations('configuration');
  const id = useId();
  const [text, setText] = useState('');
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' });
  const [disabledHere, setDisabledHere] = useState<string | null>(null);
  const parsed = parseConfiguration(text);
  const unsaveable = disabledHere !== null;
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
          entry: conflict ? t(`editor.entryKind.${conflict.kind}`, { id: conflict.entry }) : '',
          reason: params.reason ?? '',
        }),
        disabled: null,
        conflict,
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

  async function save() {
    if (request === null || working || unsaveable) return;
    focusResult.current = true;
    setOutcome({ kind: 'working' });
    // Everything entered stays as it is whatever the answer: only a save
    // that stored it replaces the page's project.
    const result = await reviseImpl({
      token,
      id: projectId,
      request,
      note,
      ...configurationPayload(doc, status),
    });
    if (result.kind === 'saved') {
      setOutcome({ kind: 'saved', revision: result.project.revision });
      onSaved(result.project);
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

  return (
    <div className="flex flex-col gap-4" data-testid="configuration-panel">
      <p className="text-sm text-text-muted">{t('intro', { revision: String(latestRevision) })}</p>
      {status === 'not_recoverable' && (
        <Notice tone="warning" live={false} testId="configuration-not-recoverable">
          {t('notRecoverable', { revision: String(latestRevision) })}
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
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={working || request === null || unsaveable}
          onClick={() => {
            void save();
          }}
          data-testid="configuration-save"
        >
          {t('save')}
        </button>
      </div>
      <p className="text-sm text-text-muted">{t('saveHelp')}</p>
      {request === null && (
        <Notice tone="warning" live={false} testId="configuration-incomplete">
          {t('incomplete')}
        </Notice>
      )}
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
                          entry: t(`editor.entryKind.${conflict.kind}`, { id: conflict.entry }),
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
