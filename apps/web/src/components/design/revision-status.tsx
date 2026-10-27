'use client';

import { useTranslations } from 'next-intl';

import { useIsolate, useIsolatingTranslations } from '@/components/design/bidi';
import { useNoteText } from '@/components/design/note-text';
import {
  EXECUTION_GAPS,
  gapOf,
  rowOf,
  type DesignFinding,
  type ExecutionRefusal,
  type Readiness,
  type StepState,
} from '@/components/design/readiness';
import { Notice, StatusBadge, type Tone } from '@/components/ui';

const STEP_TONE: Record<StepState, Tone> = {
  done: 'success',
  pending: 'neutral',
  blocked: 'critical',
  stale: 'warning',
};

/**
 * The four steps from a saved revision to an execution package, each with
 * where it stands. Execution ready is always blocked for now.
 */
export function ReadinessSteps({ readiness }: { readiness: Readiness }) {
  const t = useTranslations('design.readiness');
  const steps: Array<['saved' | 'validated' | 'approved' | 'execution', StepState]> = [
    ['saved', readiness.saved],
    ['validated', readiness.validated],
    ['approved', readiness.approved],
    ['execution', readiness.execution],
  ];
  return (
    <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4" data-testid="readiness">
      {steps.map(([step, state], index) => (
        <li
          key={step}
          className="flex items-center justify-between gap-2 rounded-md border border-border-subtle p-3"
          data-testid={`readiness-${step}`}
          data-state={state}
        >
          <span className="text-sm font-medium">
            <span className="me-2 font-mono text-text-muted" dir="ltr">
              {String(index + 1)}
            </span>
            {t(`steps.${step}`)}
          </span>
          <StatusBadge tone={STEP_TONE[state]}>{t(`state.${state}`)}</StatusBadge>
        </li>
      ))}
    </ol>
  );
}

/**
 * A finding's sentence in the reader's language. An integrity check's code is
 * said from `design.finding`, after the circuit it is about; a finding made
 * from a design note is said as that note is. A code the page does not know
 * falls back to the server's English.
 */
export function useFindingText(): (finding: DesignFinding, circuit?: string | null) => string {
  const t = useIsolatingTranslations('design');
  const isolate = useIsolate();
  const noteText = useNoteText();
  return (finding, circuit) => {
    // A refused design names its refusal: said as that refusal is, after the general sentence.
    if (finding.code === 'design_refused') {
      const { refusal, subject, ...values } = finding.values ?? {};
      const reason = refusal && t.has(`errors.${refusal}`) ? t(`errors.${refusal}`, values) : '';
      const said = subject && reason ? `${isolate(subject)}: ${reason}` : reason;
      return said ? `${t('errors.design_refused')} ${said}` : t('errors.design_refused');
    }
    const key = `finding.${finding.code}`;
    if (t.has(key)) {
      const sentence = t(key, finding.values ?? {});
      return circuit ? `${isolate(circuit)}: ${sentence}` : sentence;
    }
    if (t.has(`note.${finding.code}`)) {
      return noteText({ code: finding.code, text: finding.message, params: finding.values ?? {} });
    }
    return finding.message || finding.code;
  };
}

const SEVERITY_TONE: Record<DesignFinding['severity'], Tone> = {
  blocking: 'critical',
  warning: 'warning',
  info: 'info',
};

/**
 * The findings recorded for a revision, blocking first, each saying where to
 * fix it and, where it is a load row, a way to go to that row.
 */
export function FindingsList({
  findings,
  outdated,
  onJump,
  describe = () => null,
}: {
  findings: DesignFinding[];
  /** The page has moved on from the revision these were recorded for. */
  outdated: boolean;
  onJump: (board: string, row: number | null) => void;
  /** The description of the load row a finding is about, as the schedule has it. */
  describe?: (finding: DesignFinding) => string | null;
}) {
  const t = useIsolatingTranslations('design.findings');
  const findingText = useFindingText();
  if (findings.length === 0) {
    return (
      <p className="text-sm text-text-muted" data-testid="findings-none">
        {t('none')}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {outdated && (
        <p className="text-sm text-text-muted" data-testid="findings-outdated">
          {t('outdated')}
        </p>
      )}
      <ul className="flex flex-col divide-y divide-border-subtle text-sm" data-testid="findings">
        {findings.map((finding, index) => {
          const row = rowOf(finding);
          const where = [
            finding.board_id ? t('where.board', { board: finding.board_id }) : null,
            finding.circuit_id ? t('where.circuit', { circuit: finding.circuit_id }) : null,
            finding.device_id ? t('where.device', { device: finding.device_id }) : null,
          ].filter((part): part is string => part !== null);
          return (
            <li
              key={`${finding.code}-${String(index)}`}
              className="flex flex-wrap items-start gap-2 py-2"
              data-testid={`finding-${finding.severity}`}
            >
              <StatusBadge tone={SEVERITY_TONE[finding.severity]}>
                {t(`severity.${finding.severity}`)}
              </StatusBadge>
              <div className="min-w-0 flex-1">
                <p>{findingText(finding, describe(finding))}</p>
                {where.length > 0 && <p className="text-text-muted">{where.join(' · ')}</p>}
                {finding.requires_engineering_confirmation && (
                  <p className="text-text-muted">{t('confirm')}</p>
                )}
              </div>
              {finding.board_id && (
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  data-testid="finding-jump"
                  onClick={() => {
                    if (finding.board_id) onJump(finding.board_id, row);
                  }}
                >
                  {row === null ? t('jumpBoard') : t('jumpRow', { row: row + 1 })}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Why an execution export is blocked: what the package lacks, and what else
 * stands in the way. `refused` once an export was asked for and refused;
 * otherwise these are the reasons the saved revision lists beforehand.
 */
export function ExecutionRefused({
  refusal,
  refused = true,
}: {
  refusal: ExecutionRefusal;
  refused?: boolean;
}) {
  const t = useIsolatingTranslations('design.exports');
  const known = new Set<string>();
  const unknown: string[] = [];
  for (const code of refusal.missing) {
    const gap = gapOf(code);
    if (gap) known.add(gap);
    else unknown.push(code);
  }
  // With no list from the server, every gap the contract names still stands.
  const gaps = refusal.missing.length === 0 ? [...EXECUTION_GAPS] : [...known];
  return (
    <Notice
      tone={refused ? 'critical' : 'warning'}
      live={refused}
      title={refused ? t('executionRefused') : t('executionBlockedTitle')}
      testId={refused ? 'execution-refused' : 'execution-blockers'}
    >
      <p>{t('executionIntro')}</p>
      <ul className="mt-1 list-disc ps-5">
        {EXECUTION_GAPS.filter((gap) => gaps.includes(gap)).map((gap) => (
          <li key={gap}>{t(`gaps.${gap}`)}</li>
        ))}
        {unknown.map((code) => (
          <li key={code}>
            <bdi dir="ltr">{code}</bdi>
          </li>
        ))}
        {refusal.blockingFindings.length > 0 && (
          <li>{t('gapBlocking', { count: refusal.blockingFindings.length })}</li>
        )}
        {refusal.notValidated && <li>{t('gapNotValidated')}</li>}
        {refusal.stale && <li>{t('gapStale')}</li>}
        {refusal.approvalMissing && <li>{t('gapApproval')}</li>}
      </ul>
    </Notice>
  );
}
