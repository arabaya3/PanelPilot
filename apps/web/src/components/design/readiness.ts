import type { components } from '@panelpilot/shared-types';

import type { Refusal, SavedProject } from '@/lib/design';

/**
 * Where a saved revision stands on the way to an execution package, and the
 * findings the server recorded when it was saved.
 *
 * Saved -> Validated (with findings) -> Review approved -> Execution ready.
 * Execution ready is never reached yet: no check proves an execution package
 * complete, so the server refuses it and names what is missing.
 */

/**
 * One finding the server recorded for a revision. Not the PLC check's
 * `ValidationFinding`, which the generated types name apart.
 */
export type DesignFinding = components['schemas']['DesignFinding'];
export type FindingSeverity = DesignFinding['severity'];
/** What a saved revision can be exported as now, as the API says. */
export type ExportStatus = components['schemas']['ExportStatus'];

/** What the server recorded about the revision open. */
export type RevisionValidation = {
  /** False for a revision saved before validation: it must be saved again. */
  validated: boolean;
  findings: DesignFinding[];
  snapshotSha256: string | null;
  approvedSnapshotSha256: string | null;
  rulesVersion: string | null;
  exportStatus: ExportStatus;
  /** Why it cannot be exported for execution, as the API lists them. */
  executionBlockers: string[];
};

const SEVERITIES: readonly FindingSeverity[] = ['blocking', 'warning', 'info'];

/** Blocking first, then warnings, then information, each kept in the server's order. */
export function sortFindings(findings: readonly DesignFinding[]): DesignFinding[] {
  return findings
    .map((finding, index) => ({ finding, index }))
    .sort(
      (a, b) =>
        SEVERITIES.indexOf(a.finding.severity) - SEVERITIES.indexOf(b.finding.severity) ||
        a.index - b.index,
    )
    .map(({ finding }) => finding);
}

/** The validation of the revision a saved project is open at. */
export function validationOf(project: SavedProject): RevisionValidation {
  return {
    validated: project.validated,
    findings: sortFindings(project.findings ?? []),
    snapshotSha256: project.snapshot_sha256 ?? null,
    approvedSnapshotSha256: project.approved_snapshot_sha256 ?? null,
    rulesVersion: project.rules_version ?? null,
    exportStatus: project.export_status,
    executionBlockers: project.execution_blockers ?? [],
  };
}

export type StepState = 'done' | 'pending' | 'blocked' | 'stale';

export type Readiness = {
  saved: StepState;
  validated: StepState;
  approved: StepState;
  execution: 'blocked';
  /** Approval can be given now (the signer's name aside). */
  canApprove: boolean;
  /** Why approval cannot be given, most pressing first. */
  approvalBlockers: ApprovalBlocker[];
  /** The stored approval signs the snapshot exported (not a legacy or mismatched one). */
  approvalCovers: boolean;
  blockingCount: number;
};

export type ApprovalBlocker = 'unsaved' | 'dirty' | 'stale' | 'notValidated' | 'blocking';

/**
 * Where the work stands.
 *
 * `dirty`: the page has changes the revision does not hold, so neither its
 * validation nor its approval covers what is on screen. `stale`: the server
 * said the rules or the company profile changed since the revision was saved.
 */
export function readinessOf(input: {
  opened: { approval: { by: string | null; at: string } | null } | null;
  validation: RevisionValidation | null;
  dirty: boolean;
  stale: boolean;
}): Readiness {
  const { opened, validation, dirty } = input;
  // Stale when an export said so, or when the revision was read as stale.
  const stale = input.stale || validation?.exportStatus === 'stale';
  const blockingCount =
    validation?.findings.filter((finding) => finding.severity === 'blocking').length ?? 0;
  const validated = opened !== null && validation?.validated === true;
  const approvalCovers =
    opened?.approval != null &&
    validated &&
    validation.snapshotSha256 != null &&
    validation.approvedSnapshotSha256 === validation.snapshotSha256;

  const approvalBlockers: ApprovalBlocker[] = [];
  if (opened === null) approvalBlockers.push('unsaved');
  else {
    if (dirty) approvalBlockers.push('dirty');
    if (stale) approvalBlockers.push('stale');
    if (!validated) approvalBlockers.push('notValidated');
    else if (blockingCount > 0) approvalBlockers.push('blocking');
  }
  const moved = dirty || stale;

  return {
    saved: opened === null ? 'pending' : dirty ? 'stale' : 'done',
    validated: !validated ? 'pending' : moved ? 'stale' : blockingCount > 0 ? 'blocked' : 'done',
    approved: approvalCovers
      ? moved
        ? 'stale'
        : 'done'
      : blockingCount > 0
        ? 'blocked'
        : 'pending',
    execution: 'blocked',
    canApprove: opened !== null && opened.approval === null && approvalBlockers.length === 0,
    approvalBlockers,
    approvalCovers,
    blockingCount,
  };
}

/** What an execution package lacks, in a stable order, keyed as the page translates them. */
export const EXECUTION_GAPS = [
  'wiring',
  'control_schematics',
  'orderable_parts',
  'mechanical_layout',
] as const;
export type ExecutionGap = (typeof EXECUTION_GAPS)[number];

/** The API's names for the gaps (`app.design.marking.EXECUTION_PACKAGE_MISSING`). */
const GAP_OF: Record<string, ExecutionGap> = {
  wiring_and_terminal_connectivity: 'wiring',
  control_schematics: 'control_schematics',
  orderable_parts: 'orderable_parts',
  mechanical_layout: 'mechanical_layout',
};

/** The gap an API name stands for, or null for one the page does not know (still listed). */
export function gapOf(code: string): ExecutionGap | null {
  return GAP_OF[code] ?? null;
}

export type ExecutionRefusal = {
  /** What is missing from the package, as the API names them. */
  missing: string[];
  /** The codes of the blocking findings standing in the way. */
  blockingFindings: string[];
  approvalMissing: boolean;
  /** The revision holds no validated design (saved before validation). */
  notValidated: boolean;
  /** The rules or the company settings changed since it was saved. */
  stale: boolean;
};

/** A comma-separated param (the error envelope's params are strings) as its entries. */
function list(value: string | undefined): string[] {
  return (value ?? '')
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry !== '');
}

/** The reasons an execution export was refused (`execution_package_incomplete`). */
export function executionRefusalOf(refusal: Refusal): ExecutionRefusal {
  const params = refusal.params ?? {};
  const blockers = list(params.blockers);
  return {
    missing: list(params.missing),
    blockingFindings: list(params.blocking_findings),
    approvalMissing: params.approval === 'missing',
    notValidated: blockers.includes('not_validated'),
    stale: blockers.includes('stale'),
  };
}

const FINDING_BLOCKER = 'finding:';
const FLAG_BLOCKERS = new Set(['not_validated', 'stale', 'approval']);

/**
 * The same reasons as a saved project lists them (`execution_blockers`): the
 * package's gaps, then `not_validated`, `stale` and `approval` as they apply,
 * then `finding:<code>` for each blocking finding.
 */
export function executionBlockersOf(validation: RevisionValidation): ExecutionRefusal {
  const blockers = validation.executionBlockers;
  return {
    missing: blockers.filter(
      (entry) => !FLAG_BLOCKERS.has(entry) && !entry.startsWith(FINDING_BLOCKER),
    ),
    blockingFindings: blockers
      .filter((entry) => entry.startsWith(FINDING_BLOCKER))
      .map((entry) => entry.slice(FINDING_BLOCKER.length)),
    approvalMissing: blockers.includes('approval'),
    notValidated: blockers.includes('not_validated'),
    stale: blockers.includes('stale'),
  };
}

/**
 * The load row a finding is about. Circuit ids are `c<n>`, numbered from the
 * board's first load row (`c3` is the third); a device id begins with its
 * circuit's (`c3-breaker`). A board's own devices (`incomer`) have no row.
 */
export function rowOf(finding: Pick<DesignFinding, 'circuit_id' | 'device_id'>): number | null {
  const id = finding.circuit_id ?? finding.device_id ?? null;
  const match = id === null ? null : /^c(\d+)(?:-|$)/.exec(id);
  if (!match?.[1]) return null;
  const number = Number(match[1]);
  return number >= 1 ? number - 1 : null;
}
