'use client';

import { useId, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
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
  type ProjectDesignRequest,
  type Refusal,
  type SavedProject,
} from '@/lib/design';

/** The refusals a configuration is answered with that have their own sentence. */
const CONFIGURATION_REFUSALS = [
  'configuration_requires_v2',
  'configuration_claims_review',
  'configuration_ref_reserved',
  'configuration_refused',
  'configuration_too_large',
] as const;

type Outcome =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'reviewed'; review: ConfigurationReview; text: string }
  | { kind: 'saved'; revision: number }
  | { kind: 'error'; message: string; disabled: string | null };

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
 * Load a configuration, try it against the saved project's latest revision
 * (nothing is saved), review what it composes to, and save it as a configured
 * revision where this deployment can store one.
 */
export function ConfigurationPanel({
  token,
  projectId,
  latestRevision,
  request,
  note,
  dirty,
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
  /** The form differs from the saved revision the dry run designs. */
  dirty: boolean;
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
  const working = outcome.kind === 'working';

  function refusalText(refusal: Refusal): { message: string; disabled: string | null } {
    const code = refusal.code ?? '';
    if ((CONFIGURATION_REFUSALS as readonly string[]).includes(code)) {
      const params = refusal.params ?? {};
      return {
        message: t(`refusal.${code}`, {
          schema: params.written_schema ?? 'pp-design/1',
          ref: params.ref ?? '',
          fields: params.fields ?? '',
          refusal: params.refusal ?? '',
          limitOf: params.limit_of ?? '',
          limit: params.limit ?? '',
        }),
        disabled:
          code === 'configuration_requires_v2' ? (params.written_schema ?? 'pp-design/1') : null,
      };
    }
    if (code === 'revision_not_validated') {
      return { message: t('refusal.revision_not_validated'), disabled: null };
    }
    return {
      message: refusal.detail ? t('refusal.other', { detail: refusal.detail }) : t('failed'),
      disabled: null,
    };
  }

  function failed(result: { kind: 'unauthorized' } | { kind: 'failed' } | Refusal) {
    if (result.kind === 'unauthorized') {
      onUnauthorized();
      setOutcome({ kind: 'error', message: t('unauthorized'), disabled: null });
      return;
    }
    if (result.kind === 'failed') {
      setOutcome({ kind: 'error', message: t('failed'), disabled: null });
      return;
    }
    const said = refusalText(result);
    if (said.disabled !== null) setDisabledHere(said.disabled);
    setOutcome({ kind: 'error', ...said });
  }

  async function validate() {
    if (typeof parsed !== 'object' || working) return;
    setOutcome({ kind: 'working' });
    const result = await validateImpl({ token, id: projectId, configuration: parsed });
    if (result.kind === 'reviewed') {
      setDisabledHere(result.review.saveable ? null : result.review.written_schema);
      setOutcome({ kind: 'reviewed', review: result.review, text });
    } else {
      failed(result);
    }
  }

  async function save() {
    if (typeof parsed !== 'object' || request === null || working || dirty) return;
    setOutcome({ kind: 'working' });
    const result = await reviseImpl({
      token,
      id: projectId,
      request,
      note,
      configuration: parsed,
    });
    if (result.kind === 'saved') {
      setOutcome({ kind: 'saved', revision: result.project.revision });
      onSaved(result.project);
    } else {
      failed(result);
    }
  }

  const reviewed = outcome.kind === 'reviewed' ? outcome : null;
  const reviewCurrent = reviewed !== null && reviewed.text === text;

  return (
    <div className="flex flex-col gap-4" data-testid="configuration-panel">
      <ValidationScopeNote />
      <p className="text-sm text-text-muted">{t('intro', { revision: String(latestRevision) })}</p>
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
          rows={10}
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
          className="btn btn-sm btn-primary"
          disabled={typeof parsed !== 'object' || working}
          onClick={() => {
            void validate();
          }}
          data-testid="configuration-validate"
        >
          {t('validate')}
        </button>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          disabled={typeof parsed !== 'object' || working || request === null || dirty}
          onClick={() => {
            void save();
          }}
          data-testid="configuration-save"
        >
          {t('save')}
        </button>
      </div>
      <p className="text-sm text-text-muted">{t('validateHelp')}</p>
      {dirty && (
        <Notice tone="warning" live={false} testId="configuration-dirty">
          {t('dirty', { revision: String(latestRevision) })}
        </Notice>
      )}
      {disabledHere !== null && (
        <Notice tone="warning" live={false} testId="configuration-disabled">
          {t('refusal.configuration_requires_v2', { schema: disabledHere })}
        </Notice>
      )}
      {working && <LoadingState>{t('working')}</LoadingState>}
      {outcome.kind === 'error' && outcome.disabled === null && (
        <Notice tone="critical" testId="configuration-error">
          {outcome.message}
        </Notice>
      )}
      {outcome.kind === 'saved' && (
        <Notice tone="success" testId="configuration-saved">
          {t('saved', { revision: String(outcome.revision) })}
        </Notice>
      )}
      {reviewed && (
        <div className="flex flex-col gap-4" data-testid="configuration-review">
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
          <ModelReview model={reviewed.review.model} />
          <GroupedFindingsOfDesign findings={reviewed.review.findings} />
        </div>
      )}
    </div>
  );
}
