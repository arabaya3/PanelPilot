'use client';

import type { components } from '@panelpilot/shared-types';
import { useId } from 'react';

import { LadderDiagram } from './ladder-svg';
import { tokeniseProgram, type Token } from './tokenise';

type PlcValidationResult = components['schemas']['PlcValidationResult'];
type ValidationFinding = components['schemas']['ValidationFinding'];
type LadderRung = components['schemas']['LadderRung'];

/**
 * Generated or reviewed PLC code, with its verdict.
 *
 * The component the product's comparison claims superiority on, so it has to
 * deliver rather than gesture: ladder draws as SVG rungs from the structured
 * representation, not as an image or ASCII art, and Structured Text is
 * syntax-highlighted with findings attached to the lines they are about.
 *
 * The failure banner is the load-bearing part. A validation failure rendered
 * only as inline marks is a failure someone scrolls past — code on a screen
 * reads as finished work, and small red underlines are exactly what a hurried
 * engineer's eye skips. So a failed verdict puts a block above the code that
 * cannot be mistaken for decoration, and the inline marks are the detail
 * underneath it rather than the whole warning.
 *
 * `incomplete` gets the same treatment as `invalid`, deliberately. It means
 * nothing checked this, which is not a pass, and styling it as a mild note
 * would put unverified code one glance away from looking approved.
 */

/** Token kind to the class that colours it. */
const TOKEN_CLASSES: Record<Token['kind'], string> = {
  keyword: 'text-accent font-semibold',
  type: 'text-severity-info',
  literal: 'text-severity-info',
  number: 'text-severity-info',
  string: 'text-severity-warning',
  comment: 'text-text-muted italic',
  operator: 'text-text',
  identifier: 'text-text',
  plain: 'text-text',
};

/** Verdict to the classes its banner uses. */
interface StatusClasses {
  readonly border: string;
  readonly surface: string;
  readonly text: string;
}

const STATUS_CLASSES: Record<string, StatusClasses> = {
  invalid: {
    border: 'border-severity-critical',
    surface: 'bg-severity-critical-surface',
    text: 'text-severity-critical',
  },
  incomplete: {
    border: 'border-severity-warning',
    surface: 'bg-severity-warning-surface',
    text: 'text-severity-warning',
  },
  valid: {
    border: 'border-severity-info',
    surface: 'bg-severity-info-surface',
    text: 'text-severity-info',
  },
};

/**
 * Group findings by the line they are about.
 *
 * @param findings - Every finding on the result.
 * @returns Findings keyed by line number, plus those with no line.
 *
 * Findings without a line are kept separately rather than dropped or pinned to
 * line one. An unreferenced tag or an unverifiable dialect is about the whole
 * program, and attaching it to an arbitrary line would send someone looking at
 * code that is not the problem.
 */
function groupByLine(findings: readonly ValidationFinding[]): {
  byLine: Map<number, ValidationFinding[]>;
  general: ValidationFinding[];
} {
  const byLine = new Map<number, ValidationFinding[]>();
  const general: ValidationFinding[] = [];

  for (const finding of findings) {
    if (finding.line === null || finding.line === undefined) {
      general.push(finding);
      continue;
    }
    const existing = byLine.get(finding.line);
    if (existing === undefined) {
      byLine.set(finding.line, [finding]);
    } else {
      existing.push(finding);
    }
  }

  return { byLine, general };
}

/**
 * The banner shown above the code.
 *
 * @param props - The verdict and the findings behind it.
 * @returns The banner, or nothing when the code passed cleanly.
 *
 * `role="alert"` so a screen reader is told without having to reach the code
 * first. The visual block and the announcement are the same decision: a
 * failure must arrive before the content it is about, not after it.
 */
function VerdictBanner({ result, headingId }: { result: PlcValidationResult; headingId: string }) {
  const status = result.status;
  // Defaulted server-side, so the generated type has it optional. Treated as
  // empty rather than asserted non-null: a response that genuinely omitted it
  // would crash the banner, and the banner is the part that must survive.
  const findings = result.findings ?? [];
  if (status === 'valid' && findings.length === 0) {
    return null;
  }

  // Total rather than optional: an unrecognised status must still render a
  // banner, and the cautious styling is the right default for one nobody
  // anticipated.
  const classes: StatusClasses = STATUS_CLASSES[status] ??
    STATUS_CLASSES.incomplete ?? { border: '', surface: '', text: '' };
  const errors = findings.filter((f) => f.severity === 'error');
  const warnings = findings.filter((f) => f.severity === 'warning');

  const headline =
    status === 'invalid'
      ? `Validation failed — ${String(errors.length)} ${errors.length === 1 ? 'error' : 'errors'}`
      : status === 'incomplete'
        ? 'Not verified — this code was not checked'
        : `Passed with ${String(warnings.length)} ${warnings.length === 1 ? 'warning' : 'warnings'}`;

  return (
    <div
      role={status === 'valid' ? undefined : 'alert'}
      data-testid="verdict-banner"
      data-status={status}
      className={`mb-3 rounded-md border-2 ${classes.border} ${classes.surface} p-3`}
    >
      <p id={headingId} className={`text-sm font-semibold ${classes.text}`}>
        {headline}
      </p>
      {status === 'incomplete' && (
        <p className="mt-1 text-xs text-text">
          {/* Said in words, because "incomplete" alone reads as a smaller
              version of "valid" to anyone not steeped in the vocabulary. */}
          An unverified result is not a verified-correct one. Do not deploy this without checking it
          yourself.
        </p>
      )}
      <p className="mt-1 text-xs text-text-muted">Checked by {result.checked_by}</p>
    </div>
  );
}

/**
 * One line of Structured Text, with any findings attached to it.
 *
 * @param props - The line's tokens, its number, and its findings.
 * @returns The rendered line.
 */
function CodeLine({
  tokens,
  number,
  findings,
}: {
  tokens: readonly Token[];
  number: number;
  findings: readonly ValidationFinding[];
}) {
  const hasError = findings.some((f) => f.severity === 'error');
  const marked = findings.length > 0;

  return (
    <>
      <div
        data-testid={`code-line-${String(number)}`}
        data-has-finding={marked ? 'true' : undefined}
        className={`flex gap-3 ${
          hasError ? 'bg-severity-critical-surface' : marked ? 'bg-severity-warning-surface' : ''
        }`}
      >
        <span
          aria-hidden="true"
          className="w-8 shrink-0 select-none text-end font-mono text-xs text-text-muted"
        >
          {number}
        </span>
        <code className="whitespace-pre font-mono text-xs">
          {tokens.map((token, index) => (
            <span key={index} className={TOKEN_CLASSES[token.kind]}>
              {token.text}
            </span>
          ))}
        </code>
      </div>
      {findings.map((finding, index) => (
        <p
          key={index}
          data-testid={`finding-line-${String(number)}`}
          className={`ms-11 ps-3 text-xs ${
            finding.severity === 'error' ? 'text-severity-critical' : 'text-severity-warning'
          }`}
        >
          {/* Attached under the line rather than as a tooltip: a finding
              behind a hover is a finding nobody on a tablet ever sees, and
              this is read on a tablet next to a panel. */}
          Line {number}: {finding.message}
        </p>
      ))}
    </>
  );
}

/**
 * Display generated or reviewed PLC code with its validation verdict.
 *
 * @param props - What to show.
 * @returns The rendered view.
 */
export function PlcView({
  language,
  source,
  rungs,
  validation,
}: {
  language: 'structured-text' | 'ladder';
  source?: string | null;
  rungs?: readonly LadderRung[];
  validation: PlcValidationResult;
}) {
  const headingId = useId();
  const { byLine, general } = groupByLine(validation.findings ?? []);

  return (
    <section aria-labelledby={headingId} className="rounded-lg border border-border bg-surface p-4">
      <VerdictBanner result={validation} headingId={headingId} />

      {general.length > 0 && (
        <ul className="mb-3 space-y-1">
          {general.map((finding, index) => (
            <li
              key={index}
              data-testid="general-finding"
              className={`text-xs ${
                finding.severity === 'error' ? 'text-severity-critical' : 'text-severity-warning'
              }`}
            >
              {finding.message}
            </li>
          ))}
        </ul>
      )}

      {language === 'ladder' ? (
        rungs !== undefined && rungs.length > 0 ? (
          <div className="overflow-x-auto">
            <LadderDiagram rungs={rungs} label={`Ladder diagram, ${String(rungs.length)} rungs`} />
          </div>
        ) : (
          <p className="text-sm text-text-muted">No rungs to display.</p>
        )
      ) : source !== undefined && source !== null && source.length > 0 ? (
        <div className="overflow-x-auto rounded border border-border bg-surface-raised py-2">
          {tokeniseProgram(source).map((tokens, index) => (
            <CodeLine
              key={index}
              tokens={tokens}
              number={index + 1}
              findings={byLine.get(index + 1) ?? []}
            />
          ))}
        </div>
      ) : (
        <p className="text-sm text-text-muted">No code to display.</p>
      )}
    </section>
  );
}
